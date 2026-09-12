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
