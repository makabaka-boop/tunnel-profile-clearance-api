"""FastAPI 入口：隧道断面折线 vs 车辆限界多边形的净距复核 + 两期测点比对。"""

from __future__ import annotations

from fastapi import FastAPI

from .comparison import compare_profiles
from .geometry import minimum_segment_pair, round_three
from .schemas import (
    ClearanceRequest,
    ClearanceResponse,
    ClearanceSeriesRequest,
    ClearanceSeriesResponse,
    ComparedPointOut,
    CorrectionOut,
    DangerousPair,
    PlacementResult,
    PointOut,
    ProfileCompareRequest,
    ProfileCompareResponse,
    SegmentRef,
)

app = FastAPI(
    title="隧道限界复核 API",
    version="1.2.0",
    description="纯后端 JSON API：计算隧道折线与车辆限界多边形之间的全局最小净距，支持单点与批量平移位置复核；并支持同一断面两期测点的基准点对齐比对。",
)


def _segment_ref(points, index: int, *, closed: bool) -> SegmentRef:
    start = points[index]
    end = points[0] if closed and index == len(points) - 1 else points[index + 1]
    return SegmentRef(
        start_index=index,
        start=PointOut(x=start[0], y=start[1]),
        end=PointOut(x=end[0], y=end[1]),
    )


def _conclusion(tunnel_points, vehicle_points, required: int) -> ClearanceResponse:
    """对一组顶点计算净距结论（vehicle_points 为已施加平移后的坐标）。

    未舍入的双精度最小距离 + 按规则选出的唯一线段对；
    通过条件：未相交/接触，且未舍入最小距离 >= 要求净距。
    """
    tunnel = [(float(x), float(y)) for x, y in tunnel_points]
    vehicle = [(float(x), float(y)) for x, y in vehicle_points]

    distance, ti, vi = minimum_segment_pair(tunnel, vehicle)
    intersects = distance == 0.0
    passed = (not intersects) and distance >= float(required)

    return ClearanceResponse(
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
