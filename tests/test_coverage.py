"""控制点覆盖复核 /api/profiles/coverage 的行为测试。

覆盖：覆盖分析服务（纯 Python 层）契约 + TestClient 端到端行为。
"""

import math

from fastapi.testclient import TestClient

from app.coverage import analyze_coverage, nearest_polyline_segment
from app.main import app

client = TestClient(app)

# 门形测量轨迹（按序连接、不闭合），与既有接口测试共用同一几何
ARCH_POLYLINE = {
    "points": [
        {"x": 0, "y": 0},
        {"x": 0, "y": 1200},
        {"x": 1000, "y": 1200},
        {"x": 1000, "y": 0},
    ]
}


def post_coverage(payload):
    return client.post("/api/profiles/coverage", json=payload)


def coverage_payload(points, radius=200, polyline=None):
    return {
        "measured_polyline": polyline if polyline is not None else ARCH_POLYLINE,
        "control_points": points,
        "coverage_radius": radius,
    }


def _locs(resp):
    return [" -> ".join(str(p) for p in e["loc"]) for e in resp.json()["detail"]]


# ---------- 覆盖分析服务（纯 Python 层） ----------

def test_nearest_polyline_segment_picks_minimum_and_index():
    polyline = [(0.0, 0.0), (0.0, 1200.0), (1000.0, 1200.0), (1000.0, 0.0)]
    # (500,1400) 距顶边（起点索引 1）200
    d, i = nearest_polyline_segment(polyline, (500.0, 1400.0))
    assert d == 200.0
    assert i == 1
    # (200,600) 距左边（起点索引 0）200
    d, i = nearest_polyline_segment(polyline, (200.0, 600.0))
    assert d == 200.0
    assert i == 0
    # 点落在轨迹上距离 0，取较早的线段
    d, i = nearest_polyline_segment(polyline, (1000.0, 200.0))
    assert d == 0.0
    assert i == 2
    # 垂足在终点之外时退化为到端点距离：(2000,1200) 距右上角 (1000,1200) 1000
    d, i = nearest_polyline_segment(polyline, (2000.0, 1200.0))
    assert d == 1000.0
    assert i in (1, 2)  # 两线段在共享端点处并列，取较小索引
    assert i == 1


def test_service_tie_prefers_smaller_segment_index():
    # 点 (50,0) 与线段 0 ((0,0)->(100,0))、线段 1 ((100,0)->(200,0)) 的
    # 公共端点 (100? ) —— 改用共享端点：点 (100,-50) 到两条相邻线段均为 50
    polyline = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]
    d, i = nearest_polyline_segment(polyline, (100.0, -50.0))
    assert d == 50.0
    assert i == 0  # 并列取起点索引较小者


def test_service_all_covered_and_order():
    polyline = [(0.0, 0.0), (0.0, 1200.0), (1000.0, 1200.0), (1000.0, 0.0)]
    report = analyze_coverage(
        polyline,
        [("crown", 500.0, 1400.0), ("side", 200.0, 600.0), ("device", 1000.0, 200.0)],
        200,
    )
    assert report.all_covered is True
    assert report.first_uncovered_name is None
    assert [p.name for p in report.points] == ["crown", "side", "device"]
    assert [p.distance_mm for p in report.points] == [200.0, 200.0, 0.0]
    assert [p.nearest_segment_start_index for p in report.points] == [1, 0, 2]
    assert [p.covered for p in report.points] == [True, True, True]


def test_service_first_uncovered_does_not_interrupt():
    polyline = [(0.0, 0.0), (1000.0, 0.0)]
    report = analyze_coverage(
        polyline,
        [("near", 0.0, 5.0), ("miss", 0.0, 100.0), ("near2", 1000.0, 5.0)],
        10,
    )
    assert report.all_covered is False
    assert report.first_uncovered_name == "miss"
    assert len(report.points) == 3  # 遗漏不中断，仍返回完整结果
    assert [p.covered for p in report.points] == [True, False, True]


def test_service_unrounded_distance_decides_radius_boundary():
    # 点 (1001,1) 的垂足落在线段终点之外，距终点 (1000,0) 为 sqrt(2) ≈ 1.41421
    polyline = [(0.0, 0.0), (1000.0, 0.0)]
    tight = analyze_coverage(polyline, [("p", 1001.0, 1.0)], 1)
    assert tight.points[0].distance_mm == 1.414
    assert tight.points[0].covered is False  # 未舍入距离 > 1
    assert tight.all_covered is False

    loose = analyze_coverage(polyline, [("p", 1001.0, 1.0)], 2)
    assert loose.points[0].covered is True

    # 恰好落在边界：点 (0,2) 距线段恰好 2，半径 2 判覆盖
    boundary = analyze_coverage(polyline, [("q", 0.0, 2.0)], 2)
    assert boundary.points[0].covered is True
    assert boundary.points[0].distance_mm == 2.0


def test_service_zero_radius_requires_point_on_polyline():
    polyline = [(0.0, 0.0), (100.0, 0.0)]
    on = analyze_coverage(polyline, [("on", 50.0, 0.0)], 0)
    off = analyze_coverage(polyline, [("off", 50.0, 1.0)], 0)
    assert on.points[0].covered is True
    assert off.points[0].covered is False


# ---------- API：全部覆盖 ----------

def test_api_all_control_points_covered():
    resp = post_coverage(
        coverage_payload(
            [
                {"name": "拱顶", "x": 500, "y": 1400},
                {"name": "侧墙", "x": 200, "y": 600},
                {"name": "设备邻近", "x": 1000, "y": 200},
            ],
            radius=200,
        )
    )
    assert resp.status_code == 200, str(resp.json())
    body = resp.json()
    assert body["all_covered"] is True
    assert body["first_uncovered_name"] is None
    assert [p["name"] for p in body["points"]] == ["拱顶", "侧墙", "设备邻近"]
    assert [p["distance_mm"] for p in body["points"]] == [200.0, 200.0, 0.0]
    assert [p["nearest_segment_start_index"] for p in body["points"]] == [1, 0, 2]
    assert all(p["covered"] for p in body["points"])


def test_api_distance_rounding_three_decimals():
    # 点 (1001,1) 的垂足落在终点之外，距终点 sqrt(2) ≈ 1.41421 -> 1.414
    resp = post_coverage(
        coverage_payload(
            [{"name": "斜距点", "x": 1001, "y": 1}],
            radius=10,
            polyline={"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
        )
    )
    assert resp.status_code == 200
    point = resp.json()["points"][0]
    assert point["distance_mm"] == 1.414
    assert point["nearest_segment_start_index"] == 0
    assert point["covered"] is True


# ---------- API：首个遗漏仍返回完整结果 ----------

def test_api_first_uncovered_returns_full_report():
    resp = post_coverage(
        coverage_payload(
            [
                {"name": "crown-ok", "x": 500, "y": 1400},
                {"name": "side-miss", "x": 500, "y": -100},
                {"name": "device-ok", "x": 1000, "y": 200},
            ],
            radius=200,
        )
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_covered"] is False
    assert body["first_uncovered_name"] == "side-miss"
    # 首个遗漏之后的控制点仍完成计算，报告完整
    assert len(body["points"]) == 3
    assert [p["covered"] for p in body["points"]] == [True, False, True]
    # (500,-100) 的垂足在开口之外，到两个底角各 hypot(500,100) ≈ 509.902；
    # 两条线段并列（差 0 <= 1e-9），取起点索引较小者 0
    miss = body["points"][1]
    assert miss["distance_mm"] == 509.902
    assert miss["nearest_segment_start_index"] == 0


def test_api_radius_boundary_uses_unrounded_distance():
    # sqrt(2) 显示 1.414：半径 1 未覆盖，半径 2覆盖
    for radius, covered in ((1, False), (2, True)):
        resp = post_coverage(
            coverage_payload(
                [{"name": "p", "x": 1001, "y": 1}],
                radius=radius,
                polyline={"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
            )
        )
        assert resp.status_code == 200
        assert resp.json()["points"][0]["covered"] is covered
        assert resp.json()["all_covered"] is covered
    # 距离恰好等于半径判覆盖
    resp = post_coverage(
        coverage_payload(
            [{"name": "edge", "x": 0, "y": 10}],
            radius=10,
            polyline={"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
        )
    )
    assert resp.json()["points"][0]["covered"] is True
    assert resp.json()["all_covered"] is True


# ---------- API：线段并列选择稳定 ----------

def test_api_tie_picks_smaller_start_index():
    # 点 (100,-50) 到相邻线段 (0,0)->(100,0) 与 (100,0)->(200,0) 均为 50，取边0
    resp = post_coverage(
        coverage_payload(
            [{"name": "joint", "x": 100, "y": -50}],
            radius=100,
            polyline={
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 200, "y": 0},
                ]
            },
        )
    )
    assert resp.status_code == 200
    point = resp.json()["points"][0]
    assert point["distance_mm"] == 50.0
    assert point["nearest_segment_start_index"] == 0


def test_api_tie_follows_input_order_of_polyline():
    # 同一点对，交换两条线段的顺序后，选中的起点索引跟随变为 0（对应原边1）
    resp = post_coverage(
        coverage_payload(
            [{"name": "joint", "x": 100, "y": -50}],
            radius=100,
            polyline={
                "points": [
                    {"x": 200, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 0, "y": 0},
                ]
            },
        )
    )
    assert resp.status_code == 200
    point = resp.json()["points"][0]
    assert point["distance_mm"] == 50.0
    assert point["nearest_segment_start_index"] == 0


def test_api_tie_with_float_coordinate_points():
    # 覆盖分析直接面向浮点：两条等距（差 <= 1e-9）线段取起点索引较小者
    polyline = [(0.0, 0.0), (10.0, 0.0), (20.0, 1e-9), (30.0, 1e-9)]
    d, i = nearest_polyline_segment(polyline, (5.0, 100.0))
    assert math.isclose(d, 100.0, abs_tol=1e-9)
    assert i == 0


# ---------- API：无效折线 / 控制点 / 半径拒绝（422，不生成部分报告） ----------

def test_api_polyline_too_few_points_rejected():
    resp = post_coverage(
        coverage_payload(
            [{"name": "a", "x": 0, "y": 0}],
            polyline={"points": [{"x": 0, "y": 0}]},
        )
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    assert "too_few_points" in types
    assert "body -> measured_polyline -> points" in _locs(resp)
    assert "points" not in resp.json()  # 不生成部分报告


def test_api_adjacent_coincident_points_rejected():
    resp = post_coverage(
        coverage_payload(
            [{"name": "a", "x": 0, "y": 0}],
            polyline={"points": [{"x": 0, "y": 0}, {"x": 0, "y": 0}]},
        )
    )
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "duplicate_adjacent_point" in types
    assert "body -> measured_polyline -> points -> 1" in _locs(resp)
    assert "points" not in resp.json()


def test_api_blank_and_duplicate_control_point_names_rejected():
    # 空白 / 仅控制字符名称
    for blank in [" ", "\t", "\n", " ", "\x00", "\x1f"]:
        resp = post_coverage(coverage_payload([{"name": blank, "x": 0, "y": 0}]))
        assert resp.status_code == 422
        assert any(
            e["type"] == "blank_control_point_name" for e in resp.json()["detail"]
        )
        assert "body -> control_points -> 0 -> name" in _locs(resp)

    # 重名：定位到重复出现的后者
    resp = post_coverage(
        coverage_payload(
            [
                {"name": "crown", "x": 0, "y": 0},
                {"name": "side", "x": 1, "y": 1},
                {"name": "crown", "x": 2, "y": 2},
            ]
        )
    )
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "duplicate_control_point_name" in types
    assert "body -> control_points -> 2 -> name" in _locs(resp)
    assert "points" not in resp.json()


def test_api_empty_control_points_rejected():
    resp = post_coverage(coverage_payload([]))
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "too_short" in types
    assert "body -> control_points" in _locs(resp)
    assert "points" not in resp.json()


def test_api_invalid_radius_rejected():
    for bad in (-1, -100):
        resp = post_coverage(
            coverage_payload([{"name": "a", "x": 0, "y": 0}], radius=bad)
        )
        assert resp.status_code == 422
        assert any(e["loc"][-1] == "coverage_radius" for e in resp.json()["detail"])

    # 非整数 / 布尔冒充
    assert post_coverage(
        coverage_payload([{"name": "a", "x": 0, "y": 0}], radius=1.5)
    ).status_code == 422
    assert post_coverage(
        coverage_payload([{"name": "a", "x": 0, "y": 0}], radius=True)
    ).status_code == 422

    # 半径 0 合法（点在轨迹上即覆盖）
    ok = post_coverage(
        coverage_payload(
            [{"name": "on", "x": 500, "y": 0}],
            radius=0,
            polyline={"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
        )
    )
    assert ok.status_code == 200
    assert ok.json()["points"][0]["covered"] is True


def test_api_multiple_errors_reported_together_no_partial_report():
    # 折线点数不足 + 名称重名 + 空白名称 + 半径非法：一次返回，不生成报告
    resp = post_coverage(
        {
            "measured_polyline": {"points": [{"x": 0, "y": 0}]},
            "control_points": [
                {"name": "a", "x": 0, "y": 0},
                {"name": "a", "x": 1, "y": 1},
                {"name": "  \t\n", "x": 2, "y": 2},
            ],
            "coverage_radius": -5,
        }
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    assert "too_few_points" in types
    assert "duplicate_control_point_name" in types
    assert "blank_control_point_name" in types
    assert "greater_than_equal" in types
    locs = _locs(resp)
    assert "body -> measured_polyline -> points" in locs
    assert "body -> control_points -> 1 -> name" in locs
    assert "body -> control_points -> 2 -> name" in locs
    assert "body -> coverage_radius" in locs
    assert "points" not in resp.json()
    assert "first_uncovered_name" not in resp.json()


def test_api_control_point_field_types_and_bounds():
    # 坐标必须为整数、不越界
    assert post_coverage(
        coverage_payload([{"name": "a", "x": 1.5, "y": 0}])
    ).status_code == 422
    assert post_coverage(
        coverage_payload([{"name": "a", "x": True, "y": 0}])
    ).status_code == 422
    resp = post_coverage(
        coverage_payload([{"name": "a", "x": 1_000_001, "y": 0}])
    )
    assert resp.status_code == 422
    assert any(e["type"] == "coordinate_out_of_range" for e in resp.json()["detail"])

    # 多余字段 / 缺少必填字段
    resp = post_coverage(
        coverage_payload([{"name": "a", "x": 0, "y": 0, "z": 1}])
    )
    assert resp.status_code == 422
    assert any(e["type"] == "extra_forbidden" for e in resp.json()["detail"])

    resp = post_coverage({"coverage_radius": 10, "control_points": []})
    assert resp.status_code == 422
    missing = {e["loc"][-1] for e in resp.json()["detail"] if e["type"] == "missing"}
    assert "measured_polyline" in missing


def test_api_duplicate_name_with_invalid_coord_reported_together():
    # 字段级坐标错误不掩盖同组重名错误
    resp = post_coverage(
        coverage_payload(
            [
                {"name": "a", "x": 0, "y": 0},
                {"name": "a", "x": 1.5, "y": 0},
            ]
        )
    )
    assert resp.status_code == 422
    types = {e["type"] for e in resp.json()["detail"]}
    assert "duplicate_control_point_name" in types
    assert "int_type" in types
    locs = _locs(resp)
    assert "body -> control_points -> 1 -> name" in locs
    assert "body -> control_points -> 1 -> x" in locs
