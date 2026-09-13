"""一次性验收脚本：仅使用标准库，对运行中的 API 做端到端断言。

用法：
    BASE_URL=http://api:8000 python scripts/acceptance.py

退出码 0 表示全部通过，非 0 表示验收失败。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")

failures: list[str] = []


def post(path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get(path: str) -> tuple[int, dict]:
    with urllib.request.urlopen(BASE_URL + path, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name + (f": {detail}" if detail else ""))


def approx(a, b, eps=1e-9) -> bool:
    return abs(a - b) <= eps


def main() -> int:
    # 0. 健康检查
    status, body = get("/health")
    check("health 200", status == 200 and body.get("status") == "ok", str(body))

    # 1. 门形隧道折线 + 矩形限界，最小净距 200mm（多对并列，取索引较小者）
    status, body = post(
        "/api/clearance/check",
        {
            "tunnel_polyline": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 0, "y": 1200},
                    {"x": 1000, "y": 1200},
                    {"x": 1000, "y": 0},
                ]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 800, "y": 200},
                    {"x": 800, "y": 1000},
                    {"x": 200, "y": 1000},
                ]
            },
            "required_clearance": 200,
        },
    )
    check("case1 status 200", status == 200, str(body))
    if status == 200:
        check("case1 passed=true（距离恰好等于要求）", body["passed"] is True)
        check("case1 最小净距 200.000", approx(body["minimum_clearance_mm"], 200.0))
        check("case1 不相交", body["intersects"] is False)
        # 并列 200mm 中 (隧道边索引, 限界边索引) 最小者：边0 与边0，最近点 (200,200)
        pair = body["dangerous_pair"]
        check(
            "case1 危险对为隧道边0 / 限界边0",
            pair["tunnel_segment"]["start_index"] == 0
            and pair["vehicle_segment"]["start_index"] == 0,
            json.dumps(pair, ensure_ascii=False),
        )
        check(
            "case1 限界边0 为 (200,200)->(800,200)",
            pair["vehicle_segment"]["start"] == {"x": 200, "y": 200}
            and pair["vehicle_segment"]["end"] == {"x": 800, "y": 200},
        )

    # 2. 同一几何，要求 201mm，未舍入距离不足 -> 不通过
    status, body = post(
        "/api/clearance/check",
        {
            "tunnel_polyline": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 0, "y": 1200},
                    {"x": 1000, "y": 1200},
                    {"x": 1000, "y": 0},
                ]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 800, "y": 200},
                    {"x": 800, "y": 1000},
                    {"x": 200, "y": 1000},
                ]
            },
            "required_clearance": 201,
        },
    )
    check("case2 status 200", status == 200, str(body))
    if status == 200:
        check("case2 passed=false（200 < 201）", body["passed"] is False)
        check("case2 报告净距仍为 200.000", approx(body["minimum_clearance_mm"], 200.0))

    # 3. 限界与隧道边相交 -> 距离 0，不通过
    status, body = post(
        "/api/clearance/check",
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
        },
    )
    check("case3 status 200", status == 200, str(body))
    if status == 200:
        check("case3 passed=false", body["passed"] is False)
        check("case3 intersects=true", body["intersects"] is True)
        check("case3 距离 0.000", approx(body["minimum_clearance_mm"], 0.0))

    # 4. 隐式闭合边是最危险边：竖直短隧道边正对多边形闭合边
    status, body = post(
        "/api/clearance/check",
        {
            "tunnel_polyline": {
                "points": [{"x": 0, "y": 400}, {"x": 0, "y": 600}]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 400, "y": 400},
                    {"x": 400, "y": 600},
                    {"x": 200, "y": 800},
                ]
            },
            "required_clearance": 200,
        },
    )
    check("case4 status 200", status == 200, str(body))
    if status == 200:
        check("case4 passed=true（闭合边距离恰好 200）", body["passed"] is True)
        check(
            "case4 危险限界边是隐式闭合边（start_index=3）",
            body["dangerous_pair"]["vehicle_segment"]["start_index"] == 3,
            json.dumps(body["dangerous_pair"], ensure_ascii=False),
        )

    # 5. 斜向距离四舍五入：点(1000,1010) 到直线 y=x 为 10/sqrt(2) ≈ 7.07107
    status, body = post(
        "/api/clearance/check",
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
        },
    )
    check("case5 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case5 最小净距四舍五入为 7.071",
            approx(body["minimum_clearance_mm"], 7.071),
            str(body.get("minimum_clearance_mm")),
        )
        check("case5 passed=true（7.07107 >= 7，使用未舍入值判断）", body["passed"] is True)

    # 6. 字段级错误：多边形重复首点、自交、相邻点重复、坐标越界（一次返回多个错误）
    status, body = post(
        "/api/clearance/check",
        {
            "tunnel_polyline": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 0, "y": 0},
                ]
            },
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
        },
    )
    check("case6 status 422", status == 422, str(body))
    if status == 422:
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        joined = " | ".join(locs)
        check("case6 定位到 tunnel_polyline.points.1", "tunnel_polyline -> points -> 1" in joined, joined)
        check("case6 定位到 vehicle_polygon.points.4", "vehicle_polygon -> points -> 4" in joined, joined)
        check("case6 定位到 required_clearance", "required_clearance" in joined, joined)
        types = {e["type"] for e in body["detail"]}
        check("case6 含 duplicate_adjacent_point", "duplicate_adjacent_point" in types, str(types))
        check("case6 含 repeated_first_point", "repeated_first_point" in types, str(types))

    # 7. 自交多边形（蝴蝶结）单独验证
    status, body = post(
        "/api/clearance/check",
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
        },
    )
    check("case7 status 422（自交多边形）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case7 含 self_intersecting_polygon", "self_intersecting_polygon" in types, str(types))

    # 8. 批量位置：全部通过，结果按输入顺序，危险边端点展示平移后坐标
    status, body = post(
        "/api/clearance/check-series",
        {
            "tunnel_polyline": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 0, "y": 1200},
                    {"x": 1000, "y": 1200},
                    {"x": 1000, "y": 0},
                ]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 800, "y": 200},
                    {"x": 800, "y": 1000},
                    {"x": 200, "y": 1000},
                ]
            },
            "required_clearance": 150,
            "placements": [
                {"name": "a", "dx": 0, "dy": 0},
                {"name": "b", "dx": 50, "dy": 0},
                {"name": "c", "dx": 0, "dy": 50},
            ],
        },
    )
    check("case8 status 200", status == 200, str(body))
    if status == 200:
        check("case8 all_passed=true", body["all_passed"] is True)
        check("case8 first_failed_name 为 null", body["first_failed_name"] is None)
        check(
            "case8 结果按输入顺序返回",
            [r["name"] for r in body["results"]] == ["a", "b", "c"],
            json.dumps(body.get("results"), ensure_ascii=False),
        )
        check(
            "case8 各位置最小净距 200/150/150",
            [r["minimum_clearance_mm"] for r in body["results"]] == [200.0, 150.0, 150.0],
        )
        check("case8 全部位置 passed=true", all(r["passed"] for r in body["results"]))
        # b 右移 50mm：限界底边平移为 (250,200)->(850,200)，右下角距右壁 150mm
        pair_b = body["results"][1]["dangerous_pair"]
        check(
            "case8 危险边端点为平移后坐标 (250,200)->(850,200)",
            pair_b["vehicle_segment"]["start"] == {"x": 250, "y": 200}
            and pair_b["vehicle_segment"]["end"] == {"x": 850, "y": 200}
            and approx(pair_b["distance_mm"], 150.0),
            json.dumps(pair_b, ensure_ascii=False),
        )

    # 9. 批量位置：中间位置不合格不中断批次，first_failed_name 指向首个失败
    status, body = post(
        "/api/clearance/check-series",
        {
            "tunnel_polyline": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 0, "y": 1200},
                    {"x": 1000, "y": 1200},
                    {"x": 1000, "y": 0},
                ]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 800, "y": 200},
                    {"x": 800, "y": 1000},
                    {"x": 200, "y": 1000},
                ]
            },
            "required_clearance": 200,
            "placements": [
                {"name": "ok-1", "dx": 0, "dy": 0},
                {"name": "bad", "dx": -50, "dy": 0},
                {"name": "ok-2", "dx": 0, "dy": 0},
            ],
        },
    )
    check("case9 status 200", status == 200, str(body))
    if status == 200:
        check("case9 all_passed=false", body["all_passed"] is False)
        check("case9 first_failed_name=bad", body["first_failed_name"] == "bad")
        check(
            "case9 通过标记 [true, false, true]",
            [r["passed"] for r in body["results"]] == [True, False, True],
        )
        check(
            "case9 失败位置净距 150.000",
            approx(body["results"][1]["minimum_clearance_mm"], 150.0),
        )
        check(
            "case9 失败之后的位置仍完成计算（净距 200.000）",
            approx(body["results"][2]["minimum_clearance_mm"], 200.0),
        )

    # 10. 平移后坐标越界 -> 422，错误定位到对应位置的偏移字段
    status, body = post(
        "/api/clearance/check-series",
        {
            "tunnel_polyline": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 0, "y": 1200},
                    {"x": 1000, "y": 1200},
                    {"x": 1000, "y": 0},
                ]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 800, "y": 200},
                    {"x": 800, "y": 1000},
                    {"x": 200, "y": 1000},
                ]
            },
            "required_clearance": 150,
            "placements": [
                {"name": "ok", "dx": 0, "dy": 0},
                {"name": "far", "dx": 999_201, "dy": 0},
            ],
        },
    )
    check("case10 status 422（平移后越界）", status == 422, str(body))
    if status == 422:
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        joined = " | ".join(locs)
        check("case10 定位到 placements.1.dx", "placements -> 1 -> dx" in joined, joined)
        types = {e["type"] for e in body["detail"]}
        check(
            "case10 含 translated_coordinate_out_of_range",
            "translated_coordinate_out_of_range" in types,
            str(types),
        )

    # 11. 名称重复 / 空列表 / 超 50 个 -> 422
    dup_payload = {
        "tunnel_polyline": {"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]},
        "vehicle_polygon": {
            "points": [
                {"x": 200, "y": 200},
                {"x": 800, "y": 200},
                {"x": 800, "y": 1000},
                {"x": 200, "y": 1000},
            ]
        },
        "required_clearance": 100,
        "placements": [
            {"name": "a", "dx": 0, "dy": 0},
            {"name": "b", "dx": 1, "dy": 1},
            {"name": "a", "dx": 2, "dy": 2},
        ],
    }
    status, body = post("/api/clearance/check-series", dup_payload)
    check("case11a status 422（名称重复）", status == 422, str(body))
    if status == 422:
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        joined = " | ".join(locs)
        check("case11a 定位到 placements.2.name", "placements -> 2 -> name" in joined, joined)
        types = {e["type"] for e in body["detail"]}
        check("case11a 含 duplicate_placement_name", "duplicate_placement_name" in types, str(types))

    status, body = post("/api/clearance/check-series", {**dup_payload, "placements": []})
    check("case11b status 422（空列表）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case11b 含 too_short", "too_short" in types, str(types))

    status, body = post(
        "/api/clearance/check-series",
        {
            **dup_payload,
            "placements": [{"name": f"p{i}", "dx": 0, "dy": 0} for i in range(51)],
        },
    )
    check("case11c status 422（超过 50 个位置）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case11c 含 too_long", "too_long" in types, str(types))

    # 12. 批量输入校验：空白名称、共线零面积车辆轮廓、重名与偏移类型错误同时报告
    series_base = {
        "tunnel_polyline": {
            "points": [
                {"x": 0, "y": 0},
                {"x": 0, "y": 1200},
                {"x": 1000, "y": 1200},
                {"x": 1000, "y": 0},
            ]
        },
        "vehicle_polygon": {
            "points": [
                {"x": 200, "y": 200},
                {"x": 800, "y": 200},
                {"x": 800, "y": 1000},
                {"x": 200, "y": 1000},
            ]
        },
        "required_clearance": 150,
    }
    status, body = post(
        "/api/clearance/check-series",
        {**series_base, "placements": [{"name": "  \t\n", "dx": 0, "dy": 0}]},
    )
    check("case12a status 422（空白位置名称）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check("case12a 含 blank_placement_name", "blank_placement_name" in types, str(types))
        check(
            "case12a 定位到 placements.0.name",
            "body -> placements -> 0 -> name" in locs,
            str(locs),
        )

    status, body = post(
        "/api/clearance/check-series",
        {
            **series_base,
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 100},
                    {"x": 500, "y": 100},
                    {"x": 1000, "y": 100},
                ]
            },
            "placements": [{"name": "line", "dx": 0, "dy": 0}],
        },
    )
    check("case12b status 422（三点共线零面积轮廓）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case12b 含 degenerate_polygon", "degenerate_polygon" in types, str(types))

    status, body = post(
        "/api/clearance/check-series",
        {
            **series_base,
            "placements": [
                {"name": "a", "dx": 0, "dy": 0},
                {"name": "a", "dx": 1.5, "dy": 0},
            ],
        },
    )
    check("case12c status 422（重名且偏移非整数）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case12c 同时含重名与整数类型错误",
            "duplicate_placement_name" in types and "int_type" in types,
            str(types),
        )
        check(
            "case12c 同时定位到名称和横向偏移",
            "body -> placements -> 1 -> name" in locs and "body -> placements -> 1 -> dx" in locs,
            str(locs),
        )

    # 13. 旧接口典型请求响应保持不变（精确匹配完整响应体）
    status, body = post(
        "/api/clearance/check",
        {
            "tunnel_polyline": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 0, "y": 1200},
                    {"x": 1000, "y": 1200},
                    {"x": 1000, "y": 0},
                ]
            },
            "vehicle_polygon": {
                "points": [
                    {"x": 200, "y": 200},
                    {"x": 800, "y": 200},
                    {"x": 800, "y": 1000},
                    {"x": 200, "y": 1000},
                ]
            },
            "required_clearance": 200,
        },
    )
    check("case12 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case12 旧接口响应体逐字段不变",
            body
            == {
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
            },
            json.dumps(body, ensure_ascii=False),
        )

    # 13. 断面比对：纯整体平移 -> 修正后全部合格
    section_baseline = [
        {"name": "L1", "x": 0, "y": 0},
        {"name": "L2", "x": 0, "y": 1200},
        {"name": "C1", "x": 500, "y": 1500},
        {"name": "R2", "x": 1000, "y": 1200},
        {"name": "R1", "x": 1000, "y": 0},
    ]
    section_shifted = [
        {"name": "L1", "x": -7, "y": 11},
        {"name": "L2", "x": -7, "y": 1211},
        {"name": "C1", "x": 493, "y": 1511},
        {"name": "R2", "x": 993, "y": 1211},
        {"name": "R1", "x": 993, "y": 11},
    ]
    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": section_baseline,
            "current_points": section_shifted,
            "reference_point": "C1",
            "tolerance": 3,
        },
    )
    check("case13 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case13 修正量为 (7, -11)",
            body["correction"] == {"dx": 7, "dy": -11},
            json.dumps(body.get("correction")),
        )
        check(
            "case13 纯整体平移全部合格",
            body["all_passed"] is True and body["exceeded_names"] == [],
        )
        check(
            "case13 修正后坐标回到基准且逐点位移为 0",
            [(p["x"], p["y"]) for p in body["points"]]
            == [(0, 0), (0, 1200), (500, 1500), (1000, 1200), (1000, 0)]
            and all(p["displacement_mm"] == 0.0 for p in body["points"]),
            json.dumps(body.get("points"), ensure_ascii=False),
        )
        check(
            "case13 全并列时最大位移取输入顺序最前者",
            body["max_displacement_name"] == "L1"
            and approx(body["max_displacement_mm"], 0.0),
        )

    # 14. 断面比对：单点真实位移被定位（C1 在整体平移之外另有 5mm 位移，
    # 基准点取纯平移的 L1，修正量不被真实位移污染）
    moved = [dict(p) for p in section_shifted]
    moved[2] = {"name": "C1", "x": 496, "y": 1515}
    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": section_baseline,
            "current_points": moved,
            "reference_point": "L1",
            "tolerance": 4,
        },
    )
    check("case14 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case14 修正量为 (7, -11)",
            body["correction"] == {"dx": 7, "dy": -11},
            json.dumps(body.get("correction")),
        )
        check("case14 all_passed=false", body["all_passed"] is False)
        check(
            "case14 超限列表唯一定位 C1",
            body["exceeded_names"] == ["C1"],
            json.dumps(body.get("exceeded_names")),
        )
        check(
            "case14 最大位移测点为 C1 且 5.000mm",
            body["max_displacement_name"] == "C1"
            and approx(body["max_displacement_mm"], 5.0),
        )
        c1 = next(p for p in body["points"] if p["name"] == "C1")
        check(
            "case14 C1 修正后坐标 (503, 1504)，位移 5.000",
            (c1["x"], c1["y"]) == (503, 1504) and approx(c1["displacement_mm"], 5.0),
            json.dumps(c1),
        )
        others = [p for p in body["points"] if p["name"] != "C1"]
        check(
            "case14 其余测点零位移未误报",
            all(p["displacement_mm"] == 0.0 for p in others),
        )

    # 15. 断面比对：最大位移并列时取输入顺序靠前者，交换顺序后结果稳定跟随
    tie_baseline = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 100, "y": 0},
        {"name": "C", "x": 200, "y": 0},
    ]
    tie_current = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 106, "y": 8},
        {"name": "C", "x": 192, "y": 6},
    ]
    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": tie_baseline,
            "current_points": tie_current,
            "reference_point": "A",
            "tolerance": 20,
        },
    )
    check("case15 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case15 并列 10.000mm 取靠前的 B",
            body["max_displacement_name"] == "B"
            and approx(body["max_displacement_mm"], 10.0),
            json.dumps(
                {"name": body.get("max_displacement_name"), "mm": body.get("max_displacement_mm")}
            ),
        )
    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": [tie_baseline[0], tie_baseline[2], tie_baseline[1]],
            "current_points": [tie_current[0], tie_current[2], tie_current[1]],
            "reference_point": "A",
            "tolerance": 20,
        },
    )
    check("case15 交换顺序 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case15 交换顺序后并列取靠前的 C",
            body["max_displacement_name"] == "C",
            json.dumps(body.get("max_displacement_name")),
        )

    # 16. 断面比对：名称顺序或数量不一致 -> 422，不生成部分报告
    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": section_baseline,
            "current_points": [section_shifted[1], section_shifted[0]] + section_shifted[2:],
            "reference_point": "C1",
            "tolerance": 3,
        },
    )
    check("case16a status 422（名称顺序不一致）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check("case16a 含 point_name_mismatch", "point_name_mismatch" in types, str(types))
        check(
            "case16a 定位到 current_points.0.name",
            "current_points -> 0 -> name" in " | ".join(locs),
            str(locs),
        )
        check("case16a 不生成部分报告", "points" not in body)

    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": section_baseline,
            "current_points": section_shifted[:4],
            "reference_point": "C1",
            "tolerance": 3,
        },
    )
    check("case16b status 422（数量不一致）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case16b 含 point_count_mismatch", "point_count_mismatch" in types, str(types))
        check("case16b 不生成部分报告", "points" not in body)

    # 17. 断面比对：基准点缺失 / 容差为负 / 修正后坐标越界 -> 422
    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": section_baseline,
            "current_points": section_shifted,
            "reference_point": "NOPE",
            "tolerance": 3,
        },
    )
    check("case17a status 422（基准点缺失）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check("case17a 含 reference_point_missing", "reference_point_missing" in types, str(types))
        check("case17a 定位到 reference_point", "reference_point" in " | ".join(locs), str(locs))

    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": section_baseline,
            "current_points": section_shifted,
            "reference_point": "C1",
            "tolerance": -1,
        },
    )
    check("case17b status 422（容差为负）", status == 422, str(body))
    if status == 422:
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check("case17b 定位到 tolerance", "tolerance" in " | ".join(locs), str(locs))

    status, body = post(
        "/api/profiles/compare",
        {
            "baseline_points": [
                {"name": "REF", "x": 0, "y": 0},
                {"name": "P1", "x": 999_000, "y": 0},
            ],
            "current_points": [
                {"name": "REF", "x": -2000, "y": 0},
                {"name": "P1", "x": 998_001, "y": 0},
            ],
            "reference_point": "REF",
            "tolerance": 3000,
        },
    )
    check("case17c status 422（修正后坐标越界）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case17c 含 corrected_coordinate_out_of_range",
            "corrected_coordinate_out_of_range" in types,
            str(types),
        )
        check(
            "case17c 定位到 current_points.1.x",
            "current_points -> 1 -> x" in " | ".join(locs),
            str(locs),
        )

    # 18. 限界影响复核：纯整体平移 -> 两期净距结论逐字段一致，变化为 0
    vehicle_rect = {
        "points": [
            {"x": 200, "y": 200},
            {"x": 800, "y": 200},
            {"x": 800, "y": 1000},
            {"x": 200, "y": 1000},
        ]
    }
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            "baseline_points": section_baseline,
            "current_points": section_shifted,
            "reference_point": "C1",
            "vehicle_polygon": vehicle_rect,
            "required_clearance": 200,
        },
    )
    check("case18 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case18 纯整体平移两期结论一致",
            body["baseline"] == body["current"],
            json.dumps(body, ensure_ascii=False),
        )
        check("case18 净距变化 0.000", approx(body["clearance_change_mm"], 0.0))
        check("case18 未转为不合格", body["became_noncompliant"] is False)
        check(
            "case18 基准期净距 200.000 且合格",
            body["baseline"]["passed"] is True
            and approx(body["baseline"]["minimum_clearance_mm"], 200.0),
        )
        check(
            "case18 危险对为隧道边0 / 限界边0（并列取索引较小者）",
            body["baseline"]["dangerous_pair"]["tunnel_segment"]["start_index"] == 0
            and body["baseline"]["dangerous_pair"]["vehicle_segment"]["start_index"] == 0,
            json.dumps(body["baseline"]["dangerous_pair"], ensure_ascii=False),
        )

    # 19. 限界影响复核：局部变形（C1 真实下沉 400mm）-> 由合格转为不合格，
    # 危险边定位到变形产生的边且并列选择稳定
    deformed = [dict(p) for p in section_shifted]
    deformed[2] = {"name": "C1", "x": 493, "y": 1111}  # 修正后 (500,1100)，距限界顶边 100mm
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            "baseline_points": section_baseline,
            "current_points": deformed,
            "reference_point": "L1",
            "vehicle_polygon": vehicle_rect,
            "required_clearance": 150,
        },
    )
    check("case19 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case19 基准期合格 / 本期不合格",
            body["baseline"]["passed"] is True and body["current"]["passed"] is False,
        )
        check("case19 由合格转为不合格", body["became_noncompliant"] is True)
        check(
            "case19 本期净距 100.000，变化 -100.000",
            approx(body["current"]["minimum_clearance_mm"], 100.0)
            and approx(body["clearance_change_mm"], -100.0),
            json.dumps(
                {
                    "current": body["current"]["minimum_clearance_mm"],
                    "change": body["clearance_change_mm"],
                }
            ),
        )
        pair = body["current"]["dangerous_pair"]
        check(
            "case19 危险边为隧道边1（修正后 (0,1200)->(500,1100)）/ 限界边2，并列取边1",
            pair["tunnel_segment"]["start_index"] == 1
            and pair["tunnel_segment"]["start"] == {"x": 0, "y": 1200}
            and pair["tunnel_segment"]["end"] == {"x": 500, "y": 1100}
            and pair["vehicle_segment"]["start_index"] == 2
            and approx(pair["distance_mm"], 100.0),
            json.dumps(pair, ensure_ascii=False),
        )

    # 20. 限界影响复核：无效输入 -> 422 并定位具体字段，不产生半份影响报告
    impact_base = {
        "baseline_points": section_baseline,
        "current_points": section_shifted,
        "reference_point": "C1",
        "vehicle_polygon": vehicle_rect,
        "required_clearance": 150,
    }

    # 20a. 任一期相邻测点重合（无效折线）
    dup_baseline = [dict(p) for p in section_baseline]
    dup_baseline[2] = {"name": "C1", "x": 0, "y": 1200}
    status, body = post(
        "/api/profiles/clearance-impact", {**impact_base, "baseline_points": dup_baseline}
    )
    check("case20a status 422（基准期相邻测点重合）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check("case20a 含 duplicate_adjacent_point", "duplicate_adjacent_point" in types, str(types))
        check(
            "case20a 定位到 baseline_points.2",
            "baseline_points -> 2" in " | ".join(locs),
            str(locs),
        )
        check("case20a 不产生半份影响报告", "baseline" not in body and "current" not in body)

    dup_current = [dict(p) for p in section_shifted]
    dup_current[1] = {"name": "L2", "x": -7, "y": 11}
    status, body = post(
        "/api/profiles/clearance-impact", {**impact_base, "current_points": dup_current}
    )
    check("case20b status 422（本期相邻测点重合）", status == 422, str(body))
    if status == 422:
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case20b 定位到 current_points.1",
            "current_points -> 1" in " | ".join(locs),
            str(locs),
        )
        check("case20b 不产生半份影响报告", "baseline" not in body)

    # 20c. 名称顺序 / 数量不一致
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            **impact_base,
            "current_points": [section_shifted[1], section_shifted[0]] + section_shifted[2:],
        },
    )
    check("case20c status 422（名称顺序不一致）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case20c 含 point_name_mismatch", "point_name_mismatch" in types, str(types))
        check("case20c 不产生半份影响报告", "baseline" not in body)

    status, body = post(
        "/api/profiles/clearance-impact",
        {**impact_base, "current_points": section_shifted[:4]},
    )
    check("case20d status 422（数量不一致）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case20d 含 point_count_mismatch", "point_count_mismatch" in types, str(types))

    # 20e. 基准点缺失
    status, body = post(
        "/api/profiles/clearance-impact", {**impact_base, "reference_point": "NOPE"}
    )
    check("case20e status 422（基准点缺失）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check("case20e 含 reference_point_missing", "reference_point_missing" in types, str(types))
        check("case20e 定位到 reference_point", "reference_point" in " | ".join(locs), str(locs))

    # 20f. 修正后坐标越界
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            **impact_base,
            "baseline_points": [
                {"name": "REF", "x": 0, "y": 0},
                {"name": "P1", "x": 999_000, "y": 0},
            ],
            "current_points": [
                {"name": "REF", "x": -2000, "y": 0},
                {"name": "P1", "x": 998_001, "y": 0},
            ],
            "reference_point": "REF",
        },
    )
    check("case20f status 422（修正后坐标越界）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case20f 含 corrected_coordinate_out_of_range",
            "corrected_coordinate_out_of_range" in types,
            str(types),
        )
        check(
            "case20f 定位到 current_points.1.x",
            "current_points -> 1 -> x" in " | ".join(locs),
            str(locs),
        )

    # 20g. 车辆轮廓无效（自交）
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            **impact_base,
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 1000, "y": 1000},
                    {"x": 1000, "y": 0},
                    {"x": 0, "y": 1000},
                ]
            },
        },
    )
    check("case20g status 422（车辆轮廓自交）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check(
            "case20g 含 self_intersecting_polygon",
            "self_intersecting_polygon" in types,
            str(types),
        )
        check("case20g 不产生半份影响报告", "baseline" not in body)

    # 20h. 单点序列无法构成折线
    status, body = post(
        "/api/profiles/clearance-impact",
        {**impact_base, "current_points": section_shifted[:1], "baseline_points": section_baseline[:1]},
    )
    check("case20h status 422（单点无法构成折线）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check("case20h 含 too_short", "too_short" in types, str(types))

    # 20i. 非法坐标与名称顺序错位并存 -> 两类错误一并返回（不遗漏并存错误）
    cur_swap = [dict(p) for p in section_shifted]
    cur_swap[0] = {"name": "L1", "x": -7.5, "y": 11}
    cur_swap = [cur_swap[1], cur_swap[0]] + cur_swap[2:]
    status, body = post(
        "/api/profiles/clearance-impact", {**impact_base, "current_points": cur_swap}
    )
    check("case20i status 422（非法坐标+名称错位）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case20i 同时含 int_type 与 point_name_mismatch",
            "int_type" in types and "point_name_mismatch" in types,
            str(types),
        )
        check(
            "case20i 同时定位坐标与名称分歧",
            "current_points -> 1 -> x" in " | ".join(locs)
            and "current_points -> 0 -> name" in " | ".join(locs),
            str(locs),
        )

    # 20j. 车辆轮廓自交与基准期相邻测点重合并存 -> 同时标出基准期重合测点
    dup_base = [dict(p) for p in section_baseline]
    dup_base[2] = {"name": "C1", "x": 0, "y": 1200}
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            **impact_base,
            "baseline_points": dup_base,
            "vehicle_polygon": {
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 1000, "y": 1000},
                    {"x": 1000, "y": 0},
                    {"x": 0, "y": 1000},
                ]
            },
        },
    )
    check("case20j status 422（轮廓自交+基准期重合）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case20j 同时含 self_intersecting_polygon 与 duplicate_adjacent_point",
            "self_intersecting_polygon" in types and "duplicate_adjacent_point" in types,
            str(types),
        )
        check(
            "case20j 标出基准期重合测点 baseline_points.2",
            "baseline_points -> 2" in " | ".join(locs),
            str(locs),
        )

    # 20k. 基准期点数不足与本期相邻测点重合并存 -> 同时定位本期无效折线
    dup_cur = [dict(p) for p in section_shifted]
    dup_cur[1] = {"name": "L2", "x": -7, "y": 11}
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            **impact_base,
            "baseline_points": section_baseline[:1],
            "current_points": dup_cur,
        },
    )
    check("case20k status 422（基准期过短+本期重合）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case20k 同时含 too_short 与 duplicate_adjacent_point",
            "too_short" in types and "duplicate_adjacent_point" in types,
            str(types),
        )
        check(
            "case20k 定位本期无效折线 current_points.1",
            "current_points -> 1" in " | ".join(locs),
            str(locs),
        )

    # 20l. 首个测点与共同基准点仅以空控制字符（NUL）命名 -> 拒绝，不生成报告
    status, body = post(
        "/api/profiles/clearance-impact",
        {
            "baseline_points": [
                {"name": "\x00", "x": 0, "y": 0},
                {"name": "P1", "x": 999_000, "y": 0},
            ],
            "current_points": [
                {"name": "\x00", "x": -2, "y": 0},
                {"name": "P1", "x": 998_998, "y": 0},
            ],
            "reference_point": "\x00",
            "vehicle_polygon": vehicle_rect,
            "required_clearance": 150,
        },
    )
    check("case20l status 422（仅控制字符命名）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        check(
            "case20l 含 blank_point_name 与 blank_reference_point",
            "blank_point_name" in types and "blank_reference_point" in types,
            str(types),
        )
        check("case20l 不生成净距影响报告", "baseline" not in body and "current" not in body)

    # 21. 控制点覆盖复核：拱顶 / 侧墙 / 设备邻近控制点全部被测量轨迹有效覆盖
    coverage_polyline = {
        "points": [
            {"x": 0, "y": 0},
            {"x": 0, "y": 1200},
            {"x": 1000, "y": 1200},
            {"x": 1000, "y": 0},
        ]
    }
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": coverage_polyline,
            "control_points": [
                {"name": "拱顶", "x": 500, "y": 1400},
                {"name": "侧墙", "x": 200, "y": 600},
                {"name": "设备邻近", "x": 1000, "y": 200},
            ],
            "coverage_radius": 200,
        },
    )
    check("case21 status 200", status == 200, str(body))
    if status == 200:
        check(
            "case21 结果按输入顺序返回",
            [p["name"] for p in body["points"]] == ["拱顶", "侧墙", "设备邻近"],
            json.dumps(body["points"], ensure_ascii=False),
        )
        check(
            "case21 距离 200/200/0 且最近线段起点索引 1/0/2",
            [p["distance_mm"] for p in body["points"]] == [200.0, 200.0, 0.0]
            and [p["nearest_segment_start_index"] for p in body["points"]] == [1, 0, 2],
            json.dumps(body["points"], ensure_ascii=False),
        )
        check("case21 全部覆盖", body["all_covered"] is True)
        check("case21 first_uncovered_name 为 null", body["first_uncovered_name"] is None)
        check("case21 各点 covered 均为 true", all(p["covered"] for p in body["points"]))

    # 22. 首个遗漏：遗漏点不中断批次，仍返回完整结果并汇总首个未覆盖名称
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": coverage_polyline,
            "control_points": [
                {"name": "拱顶-已覆盖", "x": 500, "y": 1400},
                {"name": "侧墙-遗漏", "x": 500, "y": -100},
                {"name": "设备-已覆盖", "x": 1000, "y": 200},
            ],
            "coverage_radius": 200,
        },
    )
    check("case22 status 200", status == 200, str(body))
    if status == 200:
        check("case22 all_covered=false", body["all_covered"] is False)
        check(
            "case22 first_uncovered_name=侧墙-遗漏",
            body["first_uncovered_name"] == "侧墙-遗漏",
        )
        check(
            "case22 覆盖标记 [true, false, true]，报告完整",
            [p["covered"] for p in body["points"]] == [True, False, True]
            and len(body["points"]) == 3,
            json.dumps(body["points"], ensure_ascii=False),
        )
        check(
            "case22 遗漏点距离 509.902，两底角并列取线段0",
            approx(body["points"][1]["distance_mm"], 509.902)
            and body["points"][1]["nearest_segment_start_index"] == 0,
            json.dumps(body["points"][1], ensure_ascii=False),
        )

    # 23. 并列线段选择稳定：相邻线段共享端点等距时取起点索引较小者；
    # 未舍入距离判定半径边界（sqrt(2) 显示 1.414，半径 1 不覆盖、半径 2 覆盖）
    tie_polyline = {
        "points": [
            {"x": 0, "y": 0},
            {"x": 100, "y": 0},
            {"x": 200, "y": 0},
        ]
    }
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": tie_polyline,
            "control_points": [{"name": "joint", "x": 100, "y": -50}],
            "coverage_radius": 100,
        },
    )
    check("case23a status 200", status == 200, str(body))
    if status == 200:
        check(
            "case23a 并列 50mm 取起点索引 0",
            approx(body["points"][0]["distance_mm"], 50.0)
            and body["points"][0]["nearest_segment_start_index"] == 0,
            json.dumps(body["points"][0]),
        )

    # 交换两条线段顺序后，并列选择跟随到新的起点索引 0
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": {
                "points": [
                    {"x": 200, "y": 0},
                    {"x": 100, "y": 0},
                    {"x": 0, "y": 0},
                ]
            },
            "control_points": [{"name": "joint", "x": 100, "y": -50}],
            "coverage_radius": 100,
        },
    )
    check("case23b 交换顺序后并列仍取索引 0", status == 200 and body["points"][0]["nearest_segment_start_index"] == 0, str(body))

    diag_polyline = {"points": [{"x": 0, "y": 0}, {"x": 1000, "y": 0}]}
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": diag_polyline,
            "control_points": [{"name": "p", "x": 1001, "y": 1}],
            "coverage_radius": 1,
        },
    )
    check("case23c status 200", status == 200, str(body))
    if status == 200:
        check(
            "case23c 未舍入 sqrt(2)>1：显示 1.414 但不覆盖",
            approx(body["points"][0]["distance_mm"], 1.414)
            and body["points"][0]["covered"] is False
            and body["all_covered"] is False,
            json.dumps(body["points"][0]),
        )
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": diag_polyline,
            "control_points": [{"name": "p", "x": 1001, "y": 1}],
            "coverage_radius": 2,
        },
    )
    check(
        "case23d 同一几何半径 2 覆盖；距离恰好等于半径也判覆盖",
        status == 200 and body["points"][0]["covered"] is True and body["all_covered"] is True,
        str(body),
    )
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": diag_polyline,
            "control_points": [{"name": "edge", "x": 0, "y": 10}],
            "coverage_radius": 10,
        },
    )
    check(
        "case23e 距离恰好等于半径判覆盖",
        status == 200 and body["points"][0]["covered"] is True,
        str(body),
    )

    # 24. 无效测量折线 / 控制点 / 半径 -> 422 并定位字段，不生成任何部分报告
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": {"points": [{"x": 0, "y": 0}]},
            "control_points": [
                {"name": "a", "x": 0, "y": 0},
                {"name": "a", "x": 1, "y": 1},
                {"name": "  \t\n", "x": 2, "y": 2},
            ],
            "coverage_radius": -5,
        },
    )
    check("case24a status 422（多类错误一次返回）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        joined = " | ".join(locs)
        check("case24a 含 too_few_points", "too_few_points" in types, str(types))
        check(
            "case24a 含 duplicate_control_point_name / blank_control_point_name / 半径越界",
            {"duplicate_control_point_name", "blank_control_point_name", "greater_than_equal"} <= types,
            str(types),
        )
        check(
            "case24a 定位 measured_polyline.points 与 control_points.1.name / .2.name / coverage_radius",
            "measured_polyline -> points" in joined
            and "control_points -> 1 -> name" in joined
            and "control_points -> 2 -> name" in joined
            and "coverage_radius" in joined,
            joined,
        )
        check("case24a 不生成部分报告", "points" not in body and "first_uncovered_name" not in body)

    # 相邻重合点 + 空控制点列表 + 非法半径类型
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": {"points": [{"x": 0, "y": 0}, {"x": 0, "y": 0}]},
            "control_points": [],
            "coverage_radius": 1.5,
        },
    )
    check("case24b status 422（重合点/空列表/半径非整数）", status == 422, str(body))
    if status == 422:
        types = {e["type"] for e in body["detail"]}
        locs = [" -> ".join(str(p) for p in e["loc"]) for e in body["detail"]]
        check(
            "case24b 含 duplicate_adjacent_point / too_short / int_type",
            {"duplicate_adjacent_point", "too_short", "int_type"} <= types,
            str(types),
        )
        check(
            "case24b 定位 measured_polyline.points.1 与 control_points",
            "measured_polyline -> points -> 1" in " | ".join(locs)
            and "body -> control_points" in locs,
            str(locs),
        )
        check("case24b 不生成部分报告", "points" not in body)

    # 半径 0 合法：仅轨迹上的点算覆盖
    status, body = post(
        "/api/profiles/coverage",
        {
            "measured_polyline": diag_polyline,
            "control_points": [
                {"name": "on", "x": 500, "y": 0},
                {"name": "off", "x": 500, "y": 1},
            ],
            "coverage_radius": 0,
        },
    )
    check("case24c status 200（半径 0 合法）", status == 200, str(body))
    if status == 200:
        check(
            "case24c 零半径只覆盖轨迹上的点",
            [p["covered"] for p in body["points"]] == [True, False]
            and body["first_uncovered_name"] == "off"
            and body["all_covered"] is False,
            json.dumps(body["points"], ensure_ascii=False),
        )

    print()
    if failures:
        print(f"验收失败：{len(failures)} 项")
        for f in failures:
            print(" - " + f)
        return 1
    print("全部验收通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
