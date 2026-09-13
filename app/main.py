"""FastAPI 入口：隧道断面折线 vs 车辆限界多边形的净距复核 + 两期测点比对。"""

from __future__ import annotations

from fastapi import FastAPI

from .comparison import align_to_reference, compare_profiles
from .coverage import analyze_coverage
from .geometry import minimum_segment_pair, round_three
from .schemas import (
    ClearanceImpactRequest,
    ClearanceImpactResponse,
    ClearanceRequest,
    ClearanceResponse,
    ClearanceSeriesRequest,
    ClearanceSeriesResponse,
    ComparedPointOut,
    CorrectionOut,
    CoverageRequest,
    CoverageResponse,
    DangerousPair,
    PlacementResult,
    PointCoverageOut,
    PointOut,
    ProfileCompareRequest,
    ProfileCompareResponse,
    SegmentRef,
)

app = FastAPI(
    title="隧道限界复核 API",
    version="1.4.0",
    description="纯后端 JSON API：计算隧道折线与车辆限界多边形之间的全局最小净距，支持单点与批量平移位置复核；支持同一断面两期测点的基准点对齐比对，两期测点折线的限界净距影响复核，以及设计控制点对激光测量轨迹的覆盖复核。",
)


def _segment_ref(points, index: int, *, closed: bool) -> SegmentRef:
    start = points[index]
    end = points[0] if closed and index == len(points) - 1 else points[index + 1]
    return SegmentRef(
        start_index=index,
        start=PointOut(x=start[0], y=start[1]),
        end=PointOut(x=end[0], y=end[1]),
    )


def _conclusion_with_distance(
    tunnel_points, vehicle_points, required: int
) -> tuple[ClearanceResponse, float]:
    """对一组顶点计算净距结论，并同时返回未舍入的双精度最小距离。

    未舍入距离供影响报告计算两期净距变化值；结论结构与 _conclusion 完全一致。
    """
    tunnel = [(float(x), float(y)) for x, y in tunnel_points]
    vehicle = [(float(x), float(y)) for x, y in vehicle_points]

    distance, ti, vi = minimum_segment_pair(tunnel, vehicle)
    intersects = distance == 0.0
    passed = (not intersects) and distance >= float(required)

    conclusion = ClearanceResponse(
        passed=passed,
        minimum_clearance_mm=round_three(distance),
        required_clearance_mm=required,
        intersects=intersects,
        dangerous_pair=DangerousPair(
            tunnel_segment=_segment_ref(tunnel_points, ti, closed=False),
            vehicle_segment=_segment_ref(vehicle_points, vi, closed=True),
            distance_mm=round_three(distance),
        ),
    )
    return conclusion, distance


def _conclusion(tunnel_points, vehicle_points, required: int) -> ClearanceResponse:
    """对一组顶点计算净距结论（vehicle_points 为已施加平移后的坐标）。

    未舍入的双精度最小距离 + 按规则选出的唯一线段对；
    通过条件：未相交/接触，且未舍入最小距离 >= 要求净距。
    """
    conclusion, _ = _conclusion_with_distance(tunnel_points, vehicle_points, required)
    return conclusion


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/clearance/check", response_model=ClearanceResponse)
def check_clearance(req: ClearanceRequest) -> ClearanceResponse:
    tunnel_points = [(p.x, p.y) for p in req.tunnel_polyline.points]
    vehicle_points = [(p.x, p.y) for p in req.vehicle_polygon.points]
    return _conclusion(tunnel_points, vehicle_points, req.required_clearance)


@app.post("/api/clearance/check-series", response_model=ClearanceSeriesResponse)
def check_clearance_series(req: ClearanceSeriesRequest) -> ClearanceSeriesResponse:
    tunnel_points = [(p.x, p.y) for p in req.tunnel_polyline.points]
    base_vehicle = [(p.x, p.y) for p in req.vehicle_polygon.points]

    results: list[PlacementResult] = []
    first_failed_name: str | None = None
    for placement in req.placements:
        # 车辆顶点整体平移 (dx, dy)，隧道折线不动；危险边端点展示平移后坐标
        shifted = [(x + placement.dx, y + placement.dy) for x, y in base_vehicle]
        conclusion = _conclusion(tunnel_points, shifted, req.required_clearance)
        results.append(PlacementResult(name=placement.name, **conclusion.model_dump()))
        # 单个位置不合格不中断批次，仅记录首个失败名称
        if not conclusion.passed and first_failed_name is None:
            first_failed_name = placement.name

    return ClearanceSeriesResponse(
        results=results,
        all_passed=all(item.passed for item in results),
        first_failed_name=first_failed_name,
    )


@app.post("/api/profiles/compare", response_model=ProfileCompareResponse)
def compare_profile_points(req: ProfileCompareRequest) -> ProfileCompareResponse:
    # 模型层已保证：两组名称序列一致且组内唯一、基准点唯一存在、
    # 容差非负、修正后坐标不越界；比对服务只负责纯计算
    baseline = [(p.name, p.x, p.y) for p in req.baseline_points]
    current = [(p.name, p.x, p.y) for p in req.current_points]
    result = compare_profiles(baseline, current, req.reference_point, req.tolerance)
    return ProfileCompareResponse(
        correction=CorrectionOut(dx=result.dx, dy=result.dy),
        points=[
            ComparedPointOut(
                name=point.name,
                x=point.x,
                y=point.y,
                displacement_mm=point.displacement_mm,
            )
            for point in result.points
        ],
        max_displacement_name=result.max_displacement_name,
        max_displacement_mm=result.max_displacement_mm,
        exceeded_names=result.exceeded_names,
        all_passed=result.all_passed,
    )


@app.post("/api/profiles/clearance-impact", response_model=ClearanceImpactResponse)
def clearance_impact(req: ClearanceImpactRequest) -> ClearanceImpactResponse:
    # 模型层已保证：两组名称序列一致且组内唯一、基准点唯一存在、修正后坐标不越界、
    # 两期测点均不少于 2 个且相邻不重合、车辆轮廓有效；路由只负责编排计算
    baseline = [(p.name, p.x, p.y) for p in req.baseline_points]
    current = [(p.name, p.x, p.y) for p in req.current_points]
    # 先按现有基准点规则修正本期坐标，再把两期序列分别作为不闭合折线复核净距
    _, _, corrected = align_to_reference(baseline, current, req.reference_point)

    vehicle = [(p.x, p.y) for p in req.vehicle_polygon.points]
    baseline_conclusion, baseline_distance = _conclusion_with_distance(
        [(x, y) for _, x, y in baseline], vehicle, req.required_clearance
    )
    current_conclusion, current_distance = _conclusion_with_distance(
        [(x, y) for _, x, y in corrected], vehicle, req.required_clearance
    )

    return ClearanceImpactResponse(
        baseline=baseline_conclusion,
        current=current_conclusion,
        # 净距变化值基于未舍入距离求差，输出再舍入到三位小数
        clearance_change_mm=round_three(current_distance - baseline_distance),
        became_noncompliant=baseline_conclusion.passed and not current_conclusion.passed,
    )


@app.post("/api/profiles/coverage", response_model=CoverageResponse)
def profile_coverage(req: CoverageRequest) -> CoverageResponse:
    # 模型层已保证：测量折线不少于 2 点且相邻不重合、控制点名称非空白且唯一、
    # 列表非空、覆盖半径非负；覆盖服务只负责纯计算
    polyline = [(float(p.x), float(p.y)) for p in req.measured_polyline.points]
    control_points = [(p.name, float(p.x), float(p.y)) for p in req.control_points]
    result = analyze_coverage(polyline, control_points, req.coverage_radius)
    return CoverageResponse(
        points=[
            PointCoverageOut(
                name=point.name,
                distance_mm=point.distance_mm,
                nearest_segment_start_index=point.nearest_segment_start_index,
                covered=point.covered,
            )
            for point in result.points
        ],
        first_uncovered_name=result.first_uncovered_name,
        all_covered=result.all_covered,
    )
