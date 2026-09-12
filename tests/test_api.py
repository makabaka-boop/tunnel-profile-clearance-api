"""FastAPI 端到端行为测试（TestClient，无需起服务）。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ARCH_TUNNEL = {
    "points": [
        {"x": 0, "y": 0},
        {"x": 0, "y": 1200},
        {"x": 1000, "y": 1200},
        {"x": 1000, "y": 0},
    ]
}
RECT_VEHICLE = {
    "points": [
        {"x": 200, "y": 200},
        {"x": 800, "y": 200},
        {"x": 800, "y": 1000},
        {"x": 200, "y": 1000},
    ]
}


def post(payload):
    return client.post("/api/clearance/check", json=payload)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_pass_when_distance_equals_required():
    resp = post(
        {
            "tunnel_polyline": ARCH_TUNNEL,
            "vehicle_polygon": RECT_VEHICLE,
            "required_clearance": 200,
        }
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is True
    assert body["intersects"] is False
    assert body["minimum_clearance_mm"] == 200.0
    assert body["required_clearance_mm"] == 200
    pair = body["dangerous_pair"]
    assert pair["tunnel_segment"]["start_index"] == 0
    # 多对 200mm 并列，按 (隧道索引, 限界索引) 取最小：边0 与边0 的最近点 (200,200)
    assert pair["vehicle_segment"]["start_index"] == 0
    assert pair["distance_mm"] == 200.0
    assert pair["vehicle_segment"]["start"] == {"x": 200, "y": 200}
    assert pair["vehicle_segment"]["end"] == {"x": 800, "y": 200}


def test_fail_when_distance_below_required():
    resp = post(
        {
            "tunnel_polyline": ARCH_TUNNEL,
            "vehicle_polygon": RECT_VEHICLE,
            "required_clearance": 201,
        }
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is False
    assert body["minimum_clearance_mm"] == 200.0


def test_intersection_zero_fails_even_with_zero_requirement():
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": -100, "y": -100},
                    {"x": 100, "y": -100},
                    {"x": 100, "y": 100},
                    {"x": -100, "y": 100},
                ]
            },
            "required_clearance": 0,
        }
    )
    body = resp.json()
    assert body["passed"] is False
    assert body["intersects"] is True
    assert body["minimum_clearance_mm"] == 0.0


def test_touching_counts_as_intersection():
    # 限界顶点恰好落在隧道线段上（距离 0）
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 500, "y": 0},
                    {"x": 600, "y": 100},
                    {"x": 400, "y": 100},
                ]
            },
            "required_clearance": 0,
        }
    )
    body = resp.json()
    assert body["passed"] is False
    assert body["intersects"] is True
    assert body["minimum_clearance_mm"] == 0.0


def test_diagonal_distance_rounding_half_up():
    # 点 (1000,1010) 到直线 y=x 的距离 = 10/sqrt(2) ≈ 7.0710678
    resp = post(
        {
            "tunnel_polyline": {
                "points": [{"x": -5000, "y": -5000}, {"x": 5000, "y": 5000}]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 900, "y": 1200},
                    {"x": 1100, "y": 1200},
                    {"x": 1000, "y": 1010},
                ]
            },
            "required_clearance": 7,
        }
    )
    body = resp.json()
    assert body["minimum_clearance_mm"] == 7.071
    assert body["passed"] is True  # 未舍入值 7.07107 >= 7


def test_gate_uses_unrounded_distance():
    # 构造未舍入最小距离 6.99999976mm（四舍五入后恰为 7.000）：
    # 隧道边方向 (1_414_213, 1_414_212)（δ=1 的近 45 度整数向量），
    # P=(564965,564975) 到该边的叉积 = floor(7|AB|)，
    # 距离 ≈ 6.99999976mm。响应显示 7.000，但要求 7mm 时必须基于
    # 未舍入值判不通过（该整数解由丢番图方程解出，全部坐标在 ±1e6 内）。
    payload = {
        "tunnel_polyline": {
            "points": [
                {"x": -707107, "y": -707106},
                {"x": 707106, "y": 707106},
            ]
        },
        "vehicle_polygon": {
            "points": [
                {"x": 564965, "y": 564975},
                {"x": 564965, "y": 564983},
                {"x": 564957, "y": 564975},
            ]
        },
    }
    resp = post({**payload, "required_clearance": 7})
    body = resp.json()
    assert resp.status_code == 200, str(body)
    assert body["minimum_clearance_mm"] == 7.0
    assert body["passed"] is False
    assert body["intersects"] is False

    # 同一几何要求 6mm 时通过，证明门槛比较基于未舍入值
    resp2 = post({**payload, "required_clearance": 6})
    assert resp2.status_code == 200
    assert resp2.json()["passed"] is True


def test_closed_edge_can_be_dangerous():
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 400}, {"x": 0, "y": 600}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 400, "y": 400},
                    {"x": 400, "y": 600},
                    {"x": 200, "y": 800},
                ]
            },
            "required_clearance": 200,
        }
    )
    body = resp.json()
    assert body["passed"] is True
    assert body["dangerous_pair"]["vehicle_segment"]["start_index"] == 3
    assert body["dangerous_pair"]["vehicle_segment"]["end"] == {"x": 200, "y": 200}


# ---------- 字段级错误反馈 ----------

def _locs(resp):
    return [" -> ".join(str(p) for p in e["loc"]) for e in resp.json()["detail"]]


def test_duplicate_adjacent_points_field_location():
    resp = post(
        {
            "tunnel_polyline": {
                "points": [{"x": 0, "y": 0}, {"x": 0, "y": 0}]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 0, "y": 100},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422
    locs = _locs(resp)
    assert "body -> tunnel_polyline -> points -> 1" in locs
    types = {e["type"] for e in resp.json()["detail"]}
    assert "duplicate_adjacent_point" in types


def test_repeated_first_point_rejected_with_field_location():
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 100, "y": 100},
                    {"x": 0, "y": 0},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422
    locs = _locs(resp)
    assert "body -> vehicle_polygon -> points -> 3" in locs
    types = {e["type"] for e in resp.json()["detail"]}
    assert "repeated_first_point" in types


def test_self_intersecting_polygon_rejected():
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 1000, "y": 1000},
                    {"x": 1000, "y": 0},
                    {"x": 0, "y": 1000},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "self_intersecting_polygon" for e in detail)
    assert any(e["loc"][:3] == ["body", "vehicle_polygon", "points"] for e in detail)
    # 错误信息定位到自交边索引
    msg = next(e["msg"] for e in detail if e["type"] == "self_intersecting_polygon")
    assert "0" in msg and "2" in msg


def test_too_few_points():
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 0, "y": 100},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "too_few_points" in types

    resp2 = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]},
            "vehicle_polygon": {
                "points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}]
            },
            "required_clearance": 10,
        }
    )
    assert resp2.status_code == 422
    assert any(e["type"] == "too_few_points" for e in resp2.json()["detail"])


def test_coordinate_bounds():
    resp = post(
        {
            "tunnel_polyline": {
                "points": [{"x": 0, "y": 0}, {"x": 1_000_001, "y": 0}]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 0, "y": 100},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422
    locs = _locs(resp)
    assert "body -> tunnel_polyline -> points -> 1 -> x" in locs
    types = {e["type"] for e in resp.json()["detail"]}
    assert "coordinate_out_of_range" in types


def test_required_clearance_bounds_and_integer():
    base = {
        "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}]},
        "vehicle_polygon": {
            "points": [
                {"x": 0, "y": 20},
                {"x": 100, "y": 20},
                {"x": 50, "y": 80},
            ]
        },
    }
    bad = post({**base, "required_clearance": 1_000_001})
    assert bad.status_code == 422
    assert any(
        e["loc"][-1] == "required_clearance" for e in bad.json()["detail"]
    )

    # 边界值合法
    ok = post({**base, "required_clearance": -1_000_000})
    assert ok.status_code == 200
    assert ok.json()["passed"] is True

    # 必须是整数
    not_int = post({**base, "required_clearance": 10.5})
    assert not_int.status_code == 422

    # 布尔值不得冒充整数
    not_bool = post({**base, "required_clearance": True})
    assert not_bool.status_code == 422


def test_coordinates_must_be_integers():
    resp = post(
        {
            "tunnel_polyline": {
                "points": [{"x": 0, "y": 0}, {"x": 100.0, "y": 0.5}]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 0, "y": 100},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422


def test_extra_fields_rejected():
    resp = post(
        {
            "tunnel_polyline": {
                "points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
                "closed": True,
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 0, "y": 100},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422
    assert any(e["type"] == "extra_forbidden" for e in resp.json()["detail"])


def test_multiple_errors_reported_together():
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 0, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 1000, "y": 0},
                    {"x": 0, "y": 1000},
                    {"x": 1000, "y": 1000},
                    {"x": 0, "y": 0},
                ]
            },
            "required_clearance": 2_000_000,
        }
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    # 多个字段错误应一次性返回
    assert "duplicate_adjacent_point" in types
    assert "repeated_first_point" in types
    assert "less_than_equal" in types
    # 存在脏点时不再重复报自交，避免错误噪音
    assert "self_intersecting_polygon" not in types


def test_self_intersection_skipped_when_points_dirty():
    # 既重复首点又自交时，只报结构错误
    resp = post(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 1000, "y": 1000},
                    {"x": 1000, "y": 0},
                    {"x": 0, "y": 1000},
                    {"x": 0, "y": 0},
                ]
            },
            "required_clearance": 10,
        }
    )
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "repeated_first_point" in types
    assert "self_intersecting_polygon" not in types


def test_missing_required_field_location():
    resp = post({"tunnel_polyline": ARCH_TUNNEL, "required_clearance": 10})
    assert resp.status_code == 422
    assert any(
        e["type"] == "missing" and e["loc"][-1] == "vehicle_polygon"
        for e in resp.json()["detail"]
    )


def test_open_tunnel_is_not_closed():
    # 折线两端开口：车辆横在开口处下方（顶边距基线仅 1mm）。
    # 若错误地把隧道闭合，(0,0)->(1000,0) 闭合边会产生 1mm 间距而不满足 50mm；
    # 折线不闭合时，最近点是两个底角，距离约 400mm，应当通过。
    resp = post(
        {
            "tunnel_polyline": ARCH_TUNNEL,
            "vehicle_polygon": {
                "points": [
                    {"x": 400, "y": -100},
                    {"x": 600, "y": -100},
                    {"x": 600, "y": -1},
                    {"x": 400, "y": -1},
                ]
            },
            "required_clearance": 50,
        }
    )
    body = resp.json()
    assert body["passed"] is True
    assert body["intersects"] is False
    # 最近距离是到基角 (0,0)/(1000,0) 的距离，远大于 50
    assert body["minimum_clearance_mm"] > 400.0


def test_check_endpoint_response_unchanged():
    # 旧接口典型请求的完整响应体精确不变
    resp = post(
        {
            "tunnel_polyline": ARCH_TUNNEL,
            "vehicle_polygon": RECT_VEHICLE,
            "required_clearance": 200,
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "passed": True,
        "minimum_clearance_mm": 200.0,
        "required_clearance_mm": 200,
        "intersects": False,
        "dangerous_pair": {
            "tunnel_segment": {
                "start_index": 0,
                "start": {"x": 0, "y": 0},
                "end": {"x": 0, "y": 1200},
            },
            "vehicle_segment": {
                "start_index": 0,
                "start": {"x": 200, "y": 200},
                "end": {"x": 800, "y": 200},
            },
            "distance_mm": 200.0,
        },
    }


# ---------- 批量摆放位置复核 /api/clearance/check-series ----------

def post_series(payload):
    return client.post("/api/clearance/check-series", json=payload)


def series_payload(placements, required=150):
    return {
        "tunnel_polyline": ARCH_TUNNEL,
        "vehicle_polygon": RECT_VEHICLE,
        "required_clearance": required,
        "placements": placements,
    }


def test_series_all_positions_pass_in_order():
    resp = post_series(
        series_payload(
            [
                {"name": "a", "dx": 0, "dy": 0},
                {"name": "b", "dx": 50, "dy": 0},
                {"name": "c", "dx": 0, "dy": 50},
            ],
            required=150,
        )
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_passed"] is True
    assert body["first_failed_name"] is None
    # 结果与输入顺序一一对应
    assert [r["name"] for r in body["results"]] == ["a", "b", "c"]
    assert [r["minimum_clearance_mm"] for r in body["results"]] == [200.0, 150.0, 150.0]
    assert all(r["passed"] for r in body["results"])
    assert all(r["required_clearance_mm"] == 150 for r in body["results"])
    # 危险边端点展示平移后的坐标：b 右移 50mm 后，限界右下角 (850,200) 距
    # 隧道右壁 150mm；并列 150mm 中按 (隧道索引, 限界索引) 取限界边0
    pair_b = body["results"][1]["dangerous_pair"]
    assert pair_b["tunnel_segment"]["start_index"] == 2
    assert pair_b["vehicle_segment"]["start_index"] == 0
    assert pair_b["vehicle_segment"]["start"] == {"x": 250, "y": 200}
    assert pair_b["vehicle_segment"]["end"] == {"x": 850, "y": 200}
    assert pair_b["distance_mm"] == 150.0


def test_series_first_failure_does_not_interrupt():
    resp = post_series(
        series_payload(
            [
                {"name": "ok-1", "dx": 0, "dy": 0},
                {"name": "bad", "dx": -50, "dy": 0},
                {"name": "ok-2", "dx": 0, "dy": 0},
            ],
            required=200,
        )
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_passed"] is False
    assert body["first_failed_name"] == "bad"
    assert [r["passed"] for r in body["results"]] == [True, False, True]
    assert body["results"][1]["minimum_clearance_mm"] == 150.0
    # 首个失败之后的位置仍完成计算
    assert body["results"][2]["minimum_clearance_mm"] == 200.0


def test_series_intersecting_placement_fails_but_batch_completes():
    # dx=-200 后限界左边与隧道左壁共线重叠（相交/接触），距离为 0
    resp = post_series(
        series_payload(
            [
                {"name": "touch", "dx": -200, "dy": 0},
                {"name": "ok", "dx": 0, "dy": 0},
            ],
            required=0,
        )
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_passed"] is False
    assert body["first_failed_name"] == "touch"
    first, second = body["results"]
    assert first["intersects"] is True
    assert first["passed"] is False
    assert first["minimum_clearance_mm"] == 0.0
    assert second["passed"] is True
    assert second["intersects"] is False


def test_series_matches_single_check_for_zero_offset():
    single = post(
        {
            "tunnel_polyline": ARCH_TUNNEL,
            "vehicle_polygon": RECT_VEHICLE,
            "required_clearance": 200,
        }
    ).json()
    resp = post_series(series_payload([{"name": "same", "dx": 0, "dy": 0}], required=200))
    assert resp.status_code == 200
    body = resp.json()
    # 零偏移位置的结论结构与单点接口完全一致（仅多一个名称字段）
    assert body["results"] == [{"name": "same", **single}]
    assert body["all_passed"] is True
    assert body["first_failed_name"] is None


def test_series_translated_coordinate_out_of_range():
    # 车辆最大 x 为 800，dx=999201 使平移后 x = 1,000,001 越界
    resp = post_series(
        series_payload(
            [
                {"name": "ok", "dx": 0, "dy": 0},
                {"name": "far", "dx": 999_201, "dy": 0},
            ]
        )
    )
    assert resp.status_code == 422
    locs = _locs(resp)
    assert "body -> placements -> 1 -> dx" in locs
    types = {e["type"] for e in resp.json()["detail"]}
    assert "translated_coordinate_out_of_range" in types

    # y 方向越界定位到 dy（车辆最小 y 为 200，dy=-1000201 使 y = -1,000,001）
    resp2 = post_series(series_payload([{"name": "far", "dx": 0, "dy": -1_000_201}]))
    assert resp2.status_code == 422
    assert "body -> placements -> 0 -> dy" in _locs(resp2)

    # 恰好平移到边界 ±1,000,000 是合法的
    resp3 = post_series(series_payload([{"name": "edge", "dx": 999_200, "dy": 0}]))
    assert resp3.status_code == 200


def test_series_duplicate_names_rejected():
    resp = post_series(
        series_payload(
            [
                {"name": "a", "dx": 0, "dy": 0},
                {"name": "b", "dx": 1, "dy": 1},
                {"name": "a", "dx": 2, "dy": 2},
            ]
        )
    )
    assert resp.status_code == 422
    locs = _locs(resp)
    assert "body -> placements -> 2 -> name" in locs
    types = {e["type"] for e in resp.json()["detail"]}
    assert "duplicate_placement_name" in types


def test_series_placements_count_bounds():
    # 空列表
    resp = post_series(series_payload([]))
    assert resp.status_code == 422
    assert any(e["type"] == "too_short" for e in resp.json()["detail"])

    # 超过 50 个
    resp = post_series(
        series_payload([{"name": f"p{i}", "dx": 0, "dy": 0} for i in range(51)])
    )
    assert resp.status_code == 422
    assert any(e["type"] == "too_long" for e in resp.json()["detail"])

    # 恰好 50 个合法
    resp = post_series(
        series_payload([{"name": f"p{i}", "dx": 0, "dy": 0} for i in range(50)])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 50
    assert body["all_passed"] is True


def test_series_placement_field_types():
    # dx/dy 必须是整数
    resp = post_series(series_payload([{"name": "a", "dx": 1.5, "dy": 0}]))
    assert resp.status_code == 422
    assert "body -> placements -> 0 -> dx" in _locs(resp)

    # 布尔值不得冒充整数
    resp = post_series(series_payload([{"name": "a", "dx": True, "dy": 0}]))
    assert resp.status_code == 422

    # 名称不能为空
    resp = post_series(series_payload([{"name": "", "dx": 0, "dy": 0}]))
    assert resp.status_code == 422

    # 禁止多余字段
    resp = post_series(series_payload([{"name": "a", "dx": 0, "dy": 0, "dz": 1}]))
    assert resp.status_code == 422
    assert any(e["type"] == "extra_forbidden" for e in resp.json()["detail"])

    # 缺少 placements 字段
    resp = post_series(
        {
            "tunnel_polyline": ARCH_TUNNEL,
            "vehicle_polygon": RECT_VEHICLE,
            "required_clearance": 150,
        }
    )
    assert resp.status_code == 422
    assert any(
        e["type"] == "missing" and e["loc"][-1] == "placements"
        for e in resp.json()["detail"]
    )
