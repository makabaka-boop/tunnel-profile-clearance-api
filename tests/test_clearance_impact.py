"""两期限界影响复核 /api/profiles/clearance-impact 的行为测试（TestClient）。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 门形断面（开口向下，折线不闭合），与既有接口测试共用同一几何
ARCH_BASELINE = [
    {"name": "L1", "x": 0, "y": 0},
    {"name": "L2", "x": 0, "y": 1200},
    {"name": "C1", "x": 500, "y": 1500},
    {"name": "R2", "x": 1000, "y": 1200},
    {"name": "R1", "x": 1000, "y": 0},
]
# 仪器整体平移 (-7, +11)：修正后应与基准完全重合
ARCH_SHIFTED = [
    {"name": "L1", "x": -7, "y": 11},
    {"name": "L2", "x": -7, "y": 1211},
    {"name": "C1", "x": 493, "y": 1511},
    {"name": "R2", "x": 993, "y": 1211},
    {"name": "R1", "x": 993, "y": 11},
]
RECT_VEHICLE = {
    "points": [
        {"x": 200, "y": 200},
        {"x": 800, "y": 200},
        {"x": 800, "y": 1000},
        {"x": 200, "y": 1000},
    ]
}


def post_impact(payload):
    return client.post("/api/profiles/clearance-impact", json=payload)


def impact_payload(current, baseline=None, reference="L1", required=150, vehicle=None):
    return {
        "baseline_points": baseline if baseline is not None else ARCH_BASELINE,
        "current_points": current,
        "reference_point": reference,
        "vehicle_polygon": vehicle if vehicle is not None else RECT_VEHICLE,
        "required_clearance": required,
    }


def _locs(resp):
    return [" -> ".join(str(p) for p in e["loc"]) for e in resp.json()["detail"]]


def test_pure_translation_keeps_clearance_and_conclusion():
    # 纯整体平移：修正后本期折线与基准重合，两期结论逐字段一致，净距变化为 0
    resp = post_impact(impact_payload(ARCH_SHIFTED, required=200))
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"] == body["current"]
    assert body["clearance_change_mm"] == 0.0
    assert body["became_noncompliant"] is False
    # 基准期结论：四周 200mm 并列，危险对取 (隧道边0, 限界边0)
    baseline = body["baseline"]
    assert baseline["passed"] is True
    assert baseline["intersects"] is False
    assert baseline["minimum_clearance_mm"] == 200.0
    assert baseline["required_clearance_mm"] == 200
    pair = baseline["dangerous_pair"]
    assert pair["tunnel_segment"]["start_index"] == 0
    assert pair["vehicle_segment"]["start_index"] == 0
    assert pair["distance_mm"] == 200.0


def test_local_deformation_turns_noncompliant():
    # 整体平移之外，C1 真实下沉：修正后 (500,1500) -> (500,1100)，
    # 顶点距限界顶边仅 100mm < 150mm，由合格转为不合格
    deformed = [dict(p) for p in ARCH_SHIFTED]
    deformed[2] = {"name": "C1", "x": 493, "y": 1111}
    resp = post_impact(impact_payload(deformed, required=150))
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"]["passed"] is True
    assert body["baseline"]["minimum_clearance_mm"] == 200.0
    assert body["current"]["passed"] is False
    assert body["current"]["minimum_clearance_mm"] == 100.0
    assert body["current"]["intersects"] is False
    # 本期减基准期：100 - 200 = -100
    assert body["clearance_change_mm"] == -100.0
    assert body["became_noncompliant"] is True
    # 危险边定位到变形产生的边：隧道边1 (0,1200)->(500,1100)（修正后坐标），
    # 限界边2 为顶边；隧道边2 与边1 并列 100mm，按索引取边1，选择稳定
    pair = body["current"]["dangerous_pair"]
    assert pair["tunnel_segment"]["start_index"] == 1
    assert pair["tunnel_segment"]["start"] == {"x": 0, "y": 1200}
    assert pair["tunnel_segment"]["end"] == {"x": 500, "y": 1100}
    assert pair["vehicle_segment"]["start_index"] == 2
    assert pair["distance_mm"] == 100.0


def test_intersection_drives_change_to_full_loss():
    # C1 修正后 (500,900) 落入限界内部：折线与限界相交，净距归零
    deformed = [dict(p) for p in ARCH_SHIFTED]
    deformed[2] = {"name": "C1", "x": 493, "y": 911}
    resp = post_impact(impact_payload(deformed, required=150))
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"]["intersects"] is True
    assert body["current"]["passed"] is False
    assert body["current"]["minimum_clearance_mm"] == 0.0
    assert body["clearance_change_mm"] == -200.0
    assert body["became_noncompliant"] is True


def test_both_periods_failing_is_not_a_transition():
    # 基准期已不合格时，本期继续不合格不算"由合格转为不合格"
    resp = post_impact(impact_payload(ARCH_SHIFTED, required=250))
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"]["passed"] is False
    assert body["current"]["passed"] is False
    assert body["became_noncompliant"] is False
    assert body["clearance_change_mm"] == 0.0


def test_unrounded_distance_gates_both_periods():
    # 丢番图几何（同 test_gate_uses_unrounded_distance）：未舍入净距
    # 6.99999976mm 显示 7.000，但要求 7mm 时两期都必须判不通过
    baseline = [
        {"name": "P1", "x": -707107, "y": -707106},
        {"name": "P2", "x": 707106, "y": 707106},
    ]
    current = [dict(p) for p in baseline]
    vehicle = {
        "points": [
            {"x": 564965, "y": 564975},
            {"x": 564965, "y": 564983},
            {"x": 564957, "y": 564975},
        ]
    }
    resp = post_impact(
        impact_payload(current, baseline=baseline, reference="P1", required=7, vehicle=vehicle)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"]["minimum_clearance_mm"] == 7.0
    assert body["baseline"]["passed"] is False
    assert body["current"]["passed"] is False
    assert body["became_noncompliant"] is False
    assert body["clearance_change_mm"] == 0.0

    # 同一几何要求 6mm 时两期均通过，证明门槛基于未舍入值
    resp2 = post_impact(
        impact_payload(current, baseline=baseline, reference="P1", required=6, vehicle=vehicle)
    )
    assert resp2.json()["baseline"]["passed"] is True
    assert resp2.json()["current"]["passed"] is True


def test_correction_uses_reference_point_not_first_point():
    # 基准点取 C1（非首点）：修正量由 C1 决定，其余点的整体偏移保留为净距变化
    # 基准断面与纯平移断面，基准点 C1 -> 修正后完全重合
    resp = post_impact(impact_payload(ARCH_SHIFTED, reference="C1", required=200))
    assert resp.status_code == 200
    body = resp.json()
    assert body["baseline"] == body["current"]
    assert body["clearance_change_mm"] == 0.0


# ---------- 字段级错误反馈（不产生半份影响报告） ----------


def test_adjacent_duplicate_points_rejected_in_either_period():
    # 基准期相邻测点重合
    baseline = [dict(p) for p in ARCH_BASELINE]
    baseline[2] = {"name": "C1", "x": 0, "y": 1200}
    resp = post_impact(impact_payload(ARCH_SHIFTED, baseline=baseline))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "duplicate_adjacent_point" for e in detail)
    assert "body -> baseline_points -> 2" in _locs(resp)
    assert "baseline" not in resp.json() and "current" not in resp.json()

    # 本期相邻测点重合（修正为整体平移，不改变重合关系）
    current = [dict(p) for p in ARCH_SHIFTED]
    current[1] = {"name": "L2", "x": -7, "y": 11}
    resp2 = post_impact(impact_payload(current))
    assert resp2.status_code == 422
    assert any(e["type"] == "duplicate_adjacent_point" for e in resp2.json()["detail"])
    assert "body -> current_points -> 1" in _locs(resp2)
    assert "baseline" not in resp2.json()


def test_name_and_count_mismatch_rejected():
    # 顺序不一致：定位到首个分歧的列表项
    current = [ARCH_SHIFTED[1], ARCH_SHIFTED[0]] + ARCH_SHIFTED[2:]
    resp = post_impact(impact_payload(current))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "point_name_mismatch" for e in detail)
    assert "body -> current_points -> 0 -> name" in _locs(resp)
    assert "baseline" not in resp.json()

    # 数量不一致
    resp2 = post_impact(impact_payload(ARCH_SHIFTED[:4]))
    assert resp2.status_code == 422
    assert any(e["type"] == "point_count_mismatch" for e in resp2.json()["detail"])
    assert "body -> current_points" in _locs(resp2)
    assert "baseline" not in resp2.json()


def test_duplicate_point_names_rejected():
    current = [dict(p) for p in ARCH_SHIFTED]
    current[3] = {"name": "C1", "x": 993, "y": 1211}
    resp = post_impact(impact_payload(current))
    assert resp.status_code == 422
    assert any(e["type"] == "duplicate_point_name" for e in resp.json()["detail"])
    assert "body -> current_points -> 3 -> name" in _locs(resp)


def test_reference_point_missing_rejected():
    resp = post_impact(impact_payload(ARCH_SHIFTED, reference="NOPE"))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "reference_point_missing" for e in detail)
    assert "body -> reference_point" in _locs(resp)
    assert "baseline" not in resp.json()


def test_corrected_coordinate_out_of_range_rejected():
    baseline = [
        {"name": "REF", "x": 0, "y": 0},
        {"name": "P1", "x": 999_000, "y": 0},
    ]
    # 修正量 (+2000, 0)：P1 修正后 x = 1,000,001 越界
    current = [
        {"name": "REF", "x": -2000, "y": 0},
        {"name": "P1", "x": 998_001, "y": 0},
    ]
    resp = post_impact(impact_payload(current, baseline=baseline, reference="REF"))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "corrected_coordinate_out_of_range" for e in detail)
    assert "body -> current_points -> 1 -> x" in _locs(resp)
    assert "baseline" not in resp.json()


def test_invalid_vehicle_polygon_rejected():
    # 自交（蝴蝶结）轮廓
    bowtie = {
        "points": [
            {"x": 0, "y": 0},
            {"x": 1000, "y": 1000},
            {"x": 1000, "y": 0},
            {"x": 0, "y": 1000},
        ]
    }
    resp = post_impact(impact_payload(ARCH_SHIFTED, vehicle=bowtie))
    assert resp.status_code == 422
    assert any(e["type"] == "self_intersecting_polygon" for e in resp.json()["detail"])
    assert "baseline" not in resp.json()

    # 共线零面积轮廓
    line = {"points": [{"x": 0, "y": 100}, {"x": 500, "y": 100}, {"x": 1000, "y": 100}]}
    resp2 = post_impact(impact_payload(ARCH_SHIFTED, vehicle=line))
    assert resp2.status_code == 422
    assert any(e["type"] == "degenerate_polygon" for e in resp2.json()["detail"])


def test_too_few_points_rejected():
    # 折线至少 2 个点：单点序列无法构成线段
    resp = post_impact(impact_payload([{"name": "L1", "x": -7, "y": 11}]))
    assert resp.status_code == 422
    assert any(e["type"] == "too_short" for e in resp.json()["detail"])
    assert "baseline" not in resp.json()


def test_required_clearance_validation():
    # 越界
    resp = post_impact(impact_payload(ARCH_SHIFTED, required=1_000_001))
    assert resp.status_code == 422
    assert any(e["loc"][-1] == "required_clearance" for e in resp.json()["detail"])

    # 非整数 / 布尔值
    assert post_impact(impact_payload(ARCH_SHIFTED, required=10.5)).status_code == 422
    assert post_impact(impact_payload(ARCH_SHIFTED, required=True)).status_code == 422


def test_point_field_types_and_extra_fields():
    # 坐标非整数
    current = [dict(p) for p in ARCH_SHIFTED]
    current[0] = {"name": "L1", "x": -7.5, "y": 11}
    resp = post_impact(impact_payload(current))
    assert resp.status_code == 422
    assert "body -> current_points -> 0 -> x" in _locs(resp)

    # 多余字段
    resp2 = post_impact({**impact_payload(ARCH_SHIFTED), "tolerance": 3})
    assert resp2.status_code == 422
    assert any(e["type"] == "extra_forbidden" for e in resp2.json()["detail"])

    # 缺少必填字段
    resp3 = post_impact({"baseline_points": ARCH_BASELINE})
    assert resp3.status_code == 422
    missing = {e["loc"][-1] for e in resp3.json()["detail"] if e["type"] == "missing"}
    assert {"current_points", "reference_point", "vehicle_polygon", "required_clearance"} <= missing


def test_multiple_errors_reported_together():
    # 名称分歧与相邻重合同时出现时一并返回
    baseline = [dict(p) for p in ARCH_BASELINE]
    baseline[2] = {"name": "C1", "x": 0, "y": 1200}  # 与 L2 重合
    current = [ARCH_SHIFTED[1], ARCH_SHIFTED[0]] + ARCH_SHIFTED[2:]  # 顺序错位
    resp = post_impact(impact_payload(current, baseline=baseline))
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "duplicate_adjacent_point" in types
    assert "point_name_mismatch" in types
    assert "baseline" not in resp.json()


# ---------- 字段级错误与联合错误并存（不得遗漏并存错误） ----------


def test_invalid_coordinate_and_name_order_mismatch_reported_together():
    # 非法坐标（浮点）使本期测点字段构造失败，名称顺序错位的联合错误
    # 不得因此被遗漏：坐标类型错误与 point_name_mismatch 一并返回
    current = [dict(p) for p in ARCH_SHIFTED]
    current[0] = {"name": "L1", "x": -7.5, "y": 11}  # x 非整数
    current = [current[1], current[0]] + current[2:]  # 前两项顺序错位
    resp = post_impact(impact_payload(current))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    assert "int_type" in types
    assert "point_name_mismatch" in types
    locs = _locs(resp)
    assert "body -> current_points -> 1 -> x" in locs
    assert "body -> current_points -> 0 -> name" in locs
    assert "baseline" not in resp.json()


def test_self_intersecting_vehicle_and_baseline_duplicate_reported_together():
    # 车辆轮廓自交（嵌套模型构造失败）与基准期相邻测点重合并存时，
    # 不得只报轮廓自交；基准期重合测点必须准确定位
    baseline = [dict(p) for p in ARCH_BASELINE]
    baseline[2] = {"name": "C1", "x": 0, "y": 1200}  # 与 L2 重合
    bowtie = {
        "points": [
            {"x": 0, "y": 0},
            {"x": 1000, "y": 1000},
            {"x": 1000, "y": 0},
            {"x": 0, "y": 1000},
        ]
    }
    resp = post_impact(impact_payload(ARCH_SHIFTED, baseline=baseline, vehicle=bowtie))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    assert "self_intersecting_polygon" in types
    assert "duplicate_adjacent_point" in types
    assert "body -> baseline_points -> 2" in _locs(resp)
    assert "baseline" not in resp.json()


def test_too_few_baseline_points_and_current_duplicate_reported_together():
    # 基准期只有 1 点（too_short）与本期相邻测点重合并存时，
    # 不得只反馈基准期列表过短；本期无效折线必须一并定位
    current = [dict(p) for p in ARCH_SHIFTED]
    current[1] = {"name": "L2", "x": -7, "y": 11}  # 与第 0 点重合
    resp = post_impact(
        impact_payload(current, baseline=ARCH_BASELINE[:1])
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    assert "too_short" in types
    assert "point_count_mismatch" in types
    assert "duplicate_adjacent_point" in types
    assert "body -> current_points -> 1" in _locs(resp)
    assert "baseline" not in resp.json()


@pytest.mark.parametrize("control_name", ["\x00", "\x01", "\x1f", "\x7f"])
def test_control_only_point_and_reference_names_rejected(control_name):
    # 两期首个测点与共同基准点仅以空控制字符命名时，必须拒绝，
    # 不得生成完整净距影响报告（str.strip 不会去除 NUL 等控制字符）
    baseline = [
        {"name": control_name, "x": 0, "y": 0},
        {"name": "P1", "x": 999_000, "y": 0},
    ]
    current = [
        {"name": control_name, "x": -2, "y": 0},
        {"name": "P1", "x": 998_998, "y": 0},
    ]
    payload = impact_payload(
        current, baseline=baseline, reference=control_name, required=150
    )
    resp = post_impact(payload)
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "blank_point_name" in types
    assert "blank_reference_point" in types
    locs = _locs(resp)
    assert "body -> baseline_points -> 0 -> name" in locs
    assert "body -> current_points -> 0 -> name" in locs
    assert "body -> reference_point" in locs
    assert "baseline" not in resp.json() and "current" not in resp.json()


def test_name_with_valid_content_alongside_control_char_allowed():
    # 含控制字符但同时含有效名称内容的名称不视为空名称（只拒绝"无有效名称"）
    baseline = [
        {"name": "R\x001", "x": 0, "y": 0},
        {"name": "P1", "x": 999_000, "y": 0},
    ]
    current = [
        {"name": "R\x001", "x": -2, "y": 0},
        {"name": "P1", "x": 998_998, "y": 0},
    ]
    resp = post_impact(
        impact_payload(current, baseline=baseline, reference="R\x001", required=150)
    )
    assert resp.status_code == 200
    assert resp.json()["clearance_change_mm"] == 0.0


def test_control_only_names_also_rejected_on_compare_endpoint():
    # 同一有效名称规则在断面比对接口同样生效（请求校验与影响复核共用模型）
    payload = {
        "baseline_points": [
            {"name": "\x00", "x": 0, "y": 0},
            {"name": "P1", "x": 100, "y": 0},
        ],
        "current_points": [
            {"name": "\x00", "x": 0, "y": 0},
            {"name": "P1", "x": 100, "y": 0},
        ],
        "reference_point": "\x00",
        "tolerance": 5,
    }
    resp = client.post("/api/profiles/compare", json=payload)
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "blank_point_name" in types
    assert "blank_reference_point" in types

