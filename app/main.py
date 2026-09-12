"""FastAPI 入口：隧道断面折线 vs 车辆限界多边形的净距复核。"""

from __future__ import annotations

from fastapi import FastAPI

from .geometry import minimum_segment_pair, round_three
from .schemas import ClearanceRequest, ClearanceResponse, DangerousPair, PointOut, SegmentRef

app = FastAPI(
    title="隧道限界复核 API",
    version="1.0.0",
    description="纯后端 JSON API：计算隧道折线与车辆限界多边形之间的全局最小净距。",
)


def _segment_ref(points, index: int, *, closed: bool) -> SegmentRef:
    start = points[index]
    end = points[0] if closed and index == len(points) - 1 else points[index + 1]
    return SegmentRef(
        start_index=index,
        start=PointOut(x=start[0], y=start[1]),
        end=PointOut(x=end[0], y=end[1]),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/clearance/check", response_model=ClearanceResponse)
def check_clearance(req: ClearanceRequest) -> ClearanceResponse:
    tunnel = [(float(p.x), float(p.y)) for p in req.tunnel_polyline.points]
    vehicle = [(float(p.x), float(p.y)) for p in req.vehicle_polygon.points]

    # 未舍入的双精度最小距离 + 按规则选出的唯一线段对
    distance, ti, vi = minimum_segment_pair(tunnel, vehicle)
    intersects = distance == 0.0

    required = req.required_clearance
    # 通过条件：未相交/接触，且未舍入最小距离 >= 要求净距
    passed = (not intersects) and distance >= float(required)

    tunnel_points = [(p.x, p.y) for p in req.tunnel_polyline.points]
    vehicle_points = [(p.x, p.y) for p in req.vehicle_polygon.points]

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
