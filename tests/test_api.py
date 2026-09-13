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


def test_collinear_closed_vehicle_polygon_rejected():
    resp = post_series(
        {
            "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 100},
                    {"x": 500, "y": 100},
                    {"x": 1000, "y": 100},
                ]
            },
            "required_clearance": 10,
            "placements": [{"name": "a", "dx": 0, "dy": 0}],
        }
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "degenerate_polygon" for e in detail)
    assert any(e["loc"][:3] == ["body", "vehicle_polygon", "points"] for e in detail)
    assert "results" not in resp.json()


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


def test_series_blank_placement_name_rejected():
    for name in [" ", "\t", "\n", "\r\n", " "]:
        resp = post_series(series_payload([{"name": name, "dx": 0, "dy": 0}]))
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert any(e["type"] == "blank_placement_name" for e in detail)
        assert "body -> placements -> 0 -> name" in _locs(resp)
        assert "results" not in resp.json()


def test_series_duplicate_name_and_invalid_offset_reported_together():
    resp = post_series(
        series_payload(
            [
                {"name": "a", "dx": 0, "dy": 0},
                {"name": "a", "dx": 1.5, "dy": 0},
            ]
        )
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    assert "duplicate_placement_name" in types
    assert "int_type" in types
    locs = _locs(resp)
    assert "body -> placements -> 1 -> name" in locs
    assert "body -> placements -> 1 -> dx" in locs


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


# ---------- 断面两期比对 /api/profiles/compare ----------

BASELINE_SECTION = [
    {"name": "L1", "x": 0, "y": 0},
    {"name": "L2", "x": 0, "y": 1200},
    {"name": "C1", "x": 500, "y": 1500},
    {"name": "R2", "x": 1000, "y": 1200},
    {"name": "R1", "x": 1000, "y": 0},
]


def post_compare(payload):
    return client.post("/api/profiles/compare", json=payload)


def compare_payload(current, reference="C1", tolerance=5, baseline=None):
    return {
        "baseline_points": baseline if baseline is not None else BASELINE_SECTION,
        "current_points": current,
        "reference_point": reference,
        "tolerance": tolerance,
    }


def test_compare_pure_translation_all_pass():
    # 仪器整体平移 (-7, +11)：修正后应与基准完全重合，全部合格
    current = [
        {"name": "L1", "x": -7, "y": 11},
        {"name": "L2", "x": -7, "y": 1211},
        {"name": "C1", "x": 493, "y": 1511},
        {"name": "R2", "x": 993, "y": 1211},
        {"name": "R1", "x": 993, "y": 11},
    ]
    resp = post_compare(compare_payload(current, tolerance=3))
    assert resp.status_code == 200
    body = resp.json()
    assert body["correction"] == {"dx": 7, "dy": -11}
    assert body["all_passed"] is True
    assert body["exceeded_names"] == []
    assert body["max_displacement_mm"] == 0.0
    # 全部并列 0，最大位移取输入顺序最前者
    assert body["max_displacement_name"] == "L1"
    # 修正后坐标与基准一致，逐点顺序与输入一致
    assert [(p["name"], p["x"], p["y"]) for p in body["points"]] == [
        (p["name"], p["x"], p["y"]) for p in BASELINE_SECTION
    ]
    assert all(p["displacement_mm"] == 0.0 for p in body["points"])


def test_compare_single_real_displacement_located():
    # 整体平移 (-7, +11) 之外，C1 另有真实位移 (+3, +4) -> 5mm；
    # 基准点取纯平移的 L1，修正量不被真实位移污染
    current = [
        {"name": "L1", "x": -7, "y": 11},
        {"name": "L2", "x": -7, "y": 1211},
        {"name": "C1", "x": 496, "y": 1515},
        {"name": "R2", "x": 993, "y": 1211},
        {"name": "R1", "x": 993, "y": 11},
    ]
    resp = post_compare(compare_payload(current, reference="L1", tolerance=5))
    assert resp.status_code == 200
    body = resp.json()
    assert body["correction"] == {"dx": 7, "dy": -11}
    # 位移恰好等于容差，判合格
    assert body["all_passed"] is True
    assert body["exceeded_names"] == []
    assert body["max_displacement_name"] == "C1"
    assert body["max_displacement_mm"] == 5.0
    c1 = next(p for p in body["points"] if p["name"] == "C1")
    assert (c1["x"], c1["y"]) == (503, 1504)
    assert c1["displacement_mm"] == 5.0

    # 容差收紧到 4mm 时唯一位移点 C1 被定位
    resp2 = post_compare(compare_payload(current, reference="L1", tolerance=4))
    body2 = resp2.json()
    assert body2["all_passed"] is False
    assert body2["exceeded_names"] == ["C1"]
    assert body2["max_displacement_name"] == "C1"
    assert body2["max_displacement_mm"] == 5.0


def test_compare_max_tie_prefers_earlier_input():
    baseline = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 100, "y": 0},
        {"name": "C", "x": 200, "y": 0},
    ]
    # B 位移 hypot(6,8)=10，C 位移 hypot(-8,6)=10，并列取输入顺序靠前的 B
    current = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 106, "y": 8},
        {"name": "C", "x": 192, "y": 6},
    ]
    resp = post_compare(compare_payload(current, reference="A", tolerance=20, baseline=baseline))
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_displacement_mm"] == 10.0
    assert body["max_displacement_name"] == "B"

    # 交换 B/C 输入顺序后，同一对并列位移的最大测点变为 C
    resp2 = post_compare(
        compare_payload(
            [current[0], current[2], current[1]],
            reference="A",
            tolerance=20,
            baseline=[baseline[0], baseline[2], baseline[1]],
        )
    )
    assert resp2.status_code == 200
    assert resp2.json()["max_displacement_name"] == "C"


def test_compare_displacement_rounding_and_unrounded_gate():
    baseline = [
        {"name": "REF", "x": 0, "y": 0},
        {"name": "D", "x": 0, "y": 0},
    ]
    # 位移 sqrt(2) ≈ 1.41421356：输出 1.414，容差 1 按未舍入值判超限
    current = [
        {"name": "REF", "x": 0, "y": 0},
        {"name": "D", "x": 1, "y": 1},
    ]
    resp = post_compare(compare_payload(current, reference="REF", tolerance=1, baseline=baseline))
    assert resp.status_code == 200
    body = resp.json()
    assert body["points"][1]["displacement_mm"] == 1.414
    assert body["max_displacement_mm"] == 1.414
    assert body["exceeded_names"] == ["D"]
    assert body["all_passed"] is False

    # 未舍入门槛：位移 sqrt(2000^2+1) ≈ 2000.00025，显示 2000.0 但超过容差 2000
    current2 = [
        {"name": "REF", "x": 0, "y": 0},
        {"name": "D", "x": 2000, "y": 1},
    ]
    resp2 = post_compare(compare_payload(current2, reference="REF", tolerance=2000, baseline=baseline))
    body2 = resp2.json()
    assert body2["points"][1]["displacement_mm"] == 2000.0
    assert body2["all_passed"] is False
    assert body2["exceeded_names"] == ["D"]

    # 同一几何容差 2001 时合格，证明门槛比较基于未舍入值
    resp3 = post_compare(compare_payload(current2, reference="REF", tolerance=2001, baseline=baseline))
    assert resp3.json()["all_passed"] is True


def test_compare_name_mismatch_rejected():
    # 顺序不一致：定位到首个分歧的列表项
    current = [
        {"name": "L2", "x": 0, "y": 1200},
        {"name": "L1", "x": 0, "y": 0},
        {"name": "C1", "x": 500, "y": 1500},
        {"name": "R2", "x": 1000, "y": 1200},
        {"name": "R1", "x": 1000, "y": 0},
    ]
    resp = post_compare(compare_payload(current))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "point_name_mismatch" for e in detail)
    assert "body -> current_points -> 0 -> name" in _locs(resp)
    # 不生成部分报告
    assert "points" not in resp.json()

    # 数量不一致
    resp2 = post_compare(compare_payload(BASELINE_SECTION[:4]))
    assert resp2.status_code == 422
    detail2 = resp2.json()["detail"]
    assert any(e["type"] == "point_count_mismatch" for e in detail2)
    assert "body -> current_points" in _locs(resp2)
    assert "points" not in resp2.json()


def test_compare_duplicate_names_rejected():
    # 本期组内重名（同时也与基准顺序不一致，两类错误一并返回）
    current = [
        {"name": "L1", "x": 0, "y": 0},
        {"name": "L2", "x": 0, "y": 1200},
        {"name": "C1", "x": 500, "y": 1500},
        {"name": "C1", "x": 1000, "y": 1200},
        {"name": "R1", "x": 1000, "y": 0},
    ]
    resp = post_compare(compare_payload(current))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "duplicate_point_name" for e in detail)
    assert "body -> current_points -> 3 -> name" in _locs(resp)

    # 基准组内重名
    baseline = [dict(p) for p in BASELINE_SECTION]
    baseline[4] = {"name": "R2", "x": 1000, "y": 0}
    current2 = [dict(p) for p in baseline]
    resp2 = post_compare(compare_payload(current2, baseline=baseline))
    assert resp2.status_code == 422
    assert "body -> baseline_points -> 4 -> name" in _locs(resp2)


def test_compare_reference_point_missing():
    # 两组均不存在
    resp = post_compare(compare_payload(BASELINE_SECTION, reference="NOPE"))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e["type"] == "reference_point_missing" for e in detail)
    assert "body -> reference_point" in _locs(resp)

    # 仅基准组缺失（名称不一致与基准点缺失一并报告）
    baseline = [dict(p) for p in BASELINE_SECTION]
    baseline[2] = {"name": "C2", "x": 500, "y": 1500}
    resp2 = post_compare(compare_payload(BASELINE_SECTION, baseline=baseline))
    assert resp2.status_code == 422
    detail2 = resp2.json()["detail"]
    types2 = {e["type"] for e in detail2}
    assert "reference_point_missing" in types2
    assert "point_name_mismatch" in types2


def test_compare_negative_tolerance_rejected():
    resp = post_compare(compare_payload(BASELINE_SECTION, tolerance=-1))
    assert resp.status_code == 422
    assert any(e["loc"][-1] == "tolerance" for e in resp.json()["detail"])

    # 容差 0 合法：完全无位移时合格
    resp2 = post_compare(compare_payload(BASELINE_SECTION, tolerance=0))
    assert resp2.status_code == 200
    assert resp2.json()["all_passed"] is True

    # 容差必须是整数，布尔值不得冒充整数
    assert post_compare(compare_payload(BASELINE_SECTION, tolerance=1.5)).status_code == 422
    assert post_compare(compare_payload(BASELINE_SECTION, tolerance=True)).status_code == 422


def test_compare_corrected_coordinate_out_of_range():
    baseline = [
        {"name": "REF", "x": 0, "y": 0},
        {"name": "P1", "x": 999_000, "y": 0},
    ]
    # 修正量 (+2000, 0)：P1 修正后 x = 1,000,000 恰好合法
    current = [
        {"name": "REF", "x": -2000, "y": 0},
        {"name": "P1", "x": 998_000, "y": 0},
    ]
    resp = post_compare(compare_payload(current, reference="REF", tolerance=3000, baseline=baseline))
    assert resp.status_code == 200

    # x 方向越界：P1 修正后 x = 1,000,001，定位到对应列表项的 x
    current2 = [
        {"name": "REF", "x": -2000, "y": 0},
        {"name": "P1", "x": 998_001, "y": 0},
    ]
    resp2 = post_compare(compare_payload(current2, reference="REF", tolerance=3000, baseline=baseline))
    assert resp2.status_code == 422
    assert any(
        e["type"] == "corrected_coordinate_out_of_range" for e in resp2.json()["detail"]
    )
    assert "body -> current_points -> 1 -> x" in _locs(resp2)
    assert "points" not in resp2.json()

    # y 方向越界：修正量 (0, +2000)，P1 修正后 y = 1,001,000
    current3 = [
        {"name": "REF", "x": 0, "y": -2000},
        {"name": "P1", "x": 999_000, "y": 999_000},
    ]
    resp3 = post_compare(compare_payload(current3, reference="REF", tolerance=3000, baseline=baseline))
    assert resp3.status_code == 422
    assert "body -> current_points -> 1 -> y" in _locs(resp3)


def test_compare_point_field_types():
    # 坐标必须是整数
    resp = post_compare(compare_payload([{"name": "L1", "x": 0.5, "y": 0}]))
    assert resp.status_code == 422
    assert "body -> current_points -> 0 -> x" in _locs(resp)

    # 布尔值不得冒充整数
    assert post_compare(compare_payload([{"name": "L1", "x": True, "y": 0}])).status_code == 422

    # 坐标越界
    resp = post_compare(compare_payload([{"name": "L1", "x": 1_000_001, "y": 0}]))
    assert resp.status_code == 422
    assert any(e["type"] == "coordinate_out_of_range" for e in resp.json()["detail"])

    # 空白名称
    resp = post_compare(compare_payload([{"name": "  ", "x": 0, "y": 0}]))
    assert resp.status_code == 422
    assert any(e["type"] == "blank_point_name" for e in resp.json()["detail"])
    assert "body -> current_points -> 0 -> name" in _locs(resp)

    # 多余字段
    resp = post_compare(compare_payload([{"name": "L1", "x": 0, "y": 0, "z": 1}]))
    assert resp.status_code == 422
    assert any(e["type"] == "extra_forbidden" for e in resp.json()["detail"])

    # 空列表
    resp = post_compare(compare_payload([]))
    assert resp.status_code == 422
    assert any(e["type"] == "too_short" for e in resp.json()["detail"])

    # 缺少必填字段
    resp = post_compare({"baseline_points": BASELINE_SECTION})
    assert resp.status_code == 422
    missing = {e["loc"][-1] for e in resp.json()["detail"] if e["type"] == "missing"}
    assert {"current_points", "reference_point", "tolerance"} <= missing


def test_compare_duplicate_name_and_invalid_coord_reported_together():
    # 测点字段非法时，同组重名错误仍一并收集
    current = [
        {"name": "a", "x": 0, "y": 0},
        {"name": "a", "x": 1.5, "y": 0},
    ]
    resp = post_compare(compare_payload(current, baseline=[dict(p) for p in current], reference="a"))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    types = {e["type"] for e in detail}
    assert "duplicate_point_name" in types
    assert "int_type" in types
    locs = _locs(resp)
    assert "body -> baseline_points -> 1 -> name" in locs
    assert "body -> current_points -> 1 -> x" in locs
