"""请求 / 响应的 Pydantic 模型与字段级几何校验。"""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .geometry import polygon_self_intersects

COORD_LIMIT = 1_000_000
MIN_POINTS_POLYLINE = 2
MIN_POINTS_POLYGON = 3


class PointModel(BaseModel):
    """毫米整数坐标点。"""

    model_config = ConfigDict(extra="forbid")

    x: StrictInt = Field(..., description="X 坐标（毫米，|x| <= 1,000,000）")
    y: StrictInt = Field(..., description="Y 坐标（毫米，|y| <= 1,000,000）")

    @field_validator("x", "y")
    @classmethod
    def _check_bounds(cls, value: int) -> int:
        if abs(value) > COORD_LIMIT:
            raise PydanticCustomError(
                "coordinate_out_of_range",
                f"坐标绝对值不得超过 {COORD_LIMIT} 毫米，收到 {value}",
            )
        return value


def _shape_errors(
    points: list[PointModel],
    *,
    closed_polygon: bool,
    shape_label: str,
    min_points: int,
) -> list[dict]:
    """收集单个形状的几何错误。

    loc 基于形状模型内部的相对路径（points / points.<index>），
    作为嵌套模型校验错误被父模型包装时会自动加上外层字段前缀
    （tunnel_polyline / vehicle_polygon），实现字段级定位。
    消息文本中的 shape_label 使用人类可读的形状名称。
    """
    line: list[dict] = []

    if len(points) < min_points:
        need = "至少 2 个点" if min_points == MIN_POINTS_POLYLINE else "至少 3 个点"
        line.append(
            {
                "type": PydanticCustomError(
                    "too_few_points",
                    f"{shape_label}的点数不足，{need}",
                ),
                "loc": ("points",),
            }
        )

    # 相邻点不得相同
    for k in range(1, len(points)):
        if points[k].x == points[k - 1].x and points[k].y == points[k - 1].y:
            line.append(
                {
                    "type": PydanticCustomError(
                        "duplicate_adjacent_point",
                        f"{shape_label}第 {k} 个点与前一点相同，相邻点不得重复",
                    ),
                    "loc": ("points", k),
                }
            )

    # 多边形不得重复首点作为末点（显式闭合）
    if closed_polygon and len(points) >= 2:
        if points[0].x == points[-1].x and points[0].y == points[-1].y:
            line.append(
                {
                    "type": PydanticCustomError(
                        "repeated_first_point",
                        f"{shape_label}末点与首点相同，多边形首尾隐式闭合，禁止重复首点",
                    ),
                    "loc": ("points", len(points) - 1),
                }
            )

    # 多边形不得自交（点结构有效时才检查，避免对脏数据重复报错）
    if closed_polygon and len(points) >= MIN_POINTS_POLYGON and not line:
        raw = [(float(p.x), float(p.y)) for p in points]
        pair = polygon_self_intersects(raw)
        if pair is not None:
            i, j = pair
            line.append(
                {
                    "type": PydanticCustomError(
                        "self_intersecting_polygon",
                        f"{shape_label}自交：第 {i} 边与第 {j} 边相交或接触",
                    ),
                    "loc": ("points",),
                }
            )

    return line


class TunnelProfile(BaseModel):
    """隧道断面折线：按顺序连接，但不闭合。"""

    model_config = ConfigDict(extra="forbid")

    points: list[PointModel] = Field(..., description="按顺序连接的隧道折线点（不闭合）")

    @model_validator(mode="after")
    def _validate_shape(self) -> "TunnelProfile":
        errors = _shape_errors(
            self.points,
            closed_polygon=False,
            shape_label="隧道折线",
            min_points=MIN_POINTS_POLYLINE,
        )
        if errors:
            raise ValidationError.from_exception_data(self.__class__.__name__, errors)
        return self


class VehicleGauge(BaseModel):
    """车辆限界多边形：首尾点隐式闭合。"""

    model_config = ConfigDict(extra="forbid")

    points: list[PointModel] = Field(..., description="限界多边形顶点，首尾隐式闭合，禁止重复首点")

    @model_validator(mode="after")
    def _validate_shape(self) -> "VehicleGauge":
        errors = _shape_errors(
            self.points,
            closed_polygon=True,
            shape_label="车辆限界多边形",
            min_points=MIN_POINTS_POLYGON,
        )
        if errors:
            raise ValidationError.from_exception_data(self.__class__.__name__, errors)
        return self


class ClearanceRequest(BaseModel):
    """限界复核请求。"""

    model_config = ConfigDict(extra="forbid")

    tunnel_polyline: TunnelProfile
    vehicle_polygon: VehicleGauge
    required_clearance: StrictInt = Field(
        ...,
        ge=-COORD_LIMIT,
        le=COORD_LIMIT,
        description="要求净距（毫米，整数，|required_clearance| <= 1,000,000）",
    )


class Placement(BaseModel):
    """单个摆放位置：唯一名称 + 车辆限界的整数平移量（毫米）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="摆放位置名称，同一批次内唯一")
    dx: StrictInt = Field(..., description="X 方向平移量（毫米，整数）")
    dy: StrictInt = Field(..., description="Y 方向平移量（毫米，整数）")


class ClearanceSeriesRequest(BaseModel):
    """批量摆放位置的限界复核请求：同一隧道断面与车辆限界，多个平移位置。"""

    model_config = ConfigDict(extra="forbid")

    tunnel_polyline: TunnelProfile
    vehicle_polygon: VehicleGauge
    required_clearance: StrictInt = Field(
        ...,
        ge=-COORD_LIMIT,
        le=COORD_LIMIT,
        description="要求净距（毫米，整数，|required_clearance| <= 1,000,000）",
    )
    placements: list[Placement] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="摆放位置列表（1~50 个），按输入顺序逐一复核",
    )

    @model_validator(mode="after")
    def _validate_placements(self) -> "ClearanceSeriesRequest":
        errors: list[dict] = []

        # 名称在同一批次内唯一（定位到重复出现的后者）
        seen: dict[str, int] = {}
        for idx, placement in enumerate(self.placements):
            first = seen.get(placement.name)
            if first is not None:
                errors.append(
                    {
                        "type": PydanticCustomError(
                            "duplicate_placement_name",
                            f"摆放位置名称重复：{placement.name}（首次出现于第 {first} 个位置），名称必须唯一",
                        ),
                        "loc": ("placements", idx, "name"),
                    }
                )
            else:
                seen[placement.name] = idx

        # 平移后的车辆顶点坐标不得越过既有范围（±1,000,000 毫米）；
        # 每个位置每个方向只报首个越界顶点，避免错误噪音
        vertices = [(p.x, p.y) for p in self.vehicle_polygon.points]
        for idx, placement in enumerate(self.placements):
            x_reported = y_reported = False
            for k, (vx, vy) in enumerate(vertices):
                if not x_reported and abs(vx + placement.dx) > COORD_LIMIT:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "translated_coordinate_out_of_range",
                                f"摆放位置 {placement.name} 平移后第 {k} 个顶点的 x 坐标为 "
                                f"{vx + placement.dx}，绝对值不得超过 {COORD_LIMIT} 毫米",
                            ),
                            "loc": ("placements", idx, "dx"),
                        }
                    )
                    x_reported = True
                if not y_reported and abs(vy + placement.dy) > COORD_LIMIT:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "translated_coordinate_out_of_range",
                                f"摆放位置 {placement.name} 平移后第 {k} 个顶点的 y 坐标为 "
                                f"{vy + placement.dy}，绝对值不得超过 {COORD_LIMIT} 毫米",
                            ),
                            "loc": ("placements", idx, "dy"),
                        }
                    )
                    y_reported = True
                if x_reported and y_reported:
                    break

        if errors:
            raise ValidationError.from_exception_data(self.__class__.__name__, errors)
        return self


class PointOut(BaseModel):
    x: int
    y: int


class SegmentRef(BaseModel):
    """危险线段：给出起点索引与两个端点（多边形闭合边的终点为首点）。"""

    start_index: int
    start: PointOut
    end: PointOut


class DangerousPair(BaseModel):
    """全局最危险（距离最小）的一对线段。"""

    tunnel_segment: SegmentRef
    vehicle_segment: SegmentRef
    distance_mm: float = Field(..., description="该对线段距离（毫米，保留三位小数）")


class ClearanceResponse(BaseModel):
    """限界复核结论。"""

    passed: bool = Field(..., description="true 表示未相交且未舍入最小距离 >= 要求净距")
    minimum_clearance_mm: float = Field(..., description="全局最小净距（毫米，四舍五入到三位小数）")
    required_clearance_mm: int
    intersects: bool = Field(..., description="两组线段是否相交或接触")
    dangerous_pair: DangerousPair


class PlacementResult(ClearanceResponse):
    """单个摆放位置的净距结论：既有结论结构附加位置名称。"""

    name: str = Field(..., description="对应输入摆放位置的名称")


class ClearanceSeriesResponse(BaseModel):
    """批量复核结论：results 与输入 placements 按顺序一一对应。"""

    results: list[PlacementResult]
    all_passed: bool = Field(..., description="全部位置均通过时为 true")
    first_failed_name: str | None = Field(
        ..., description="首个不通过位置的名称；全部通过时为 null"
    )
