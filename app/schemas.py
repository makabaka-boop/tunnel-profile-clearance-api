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

from .geometry import polygon_is_degenerate, polygon_self_intersects

COORD_LIMIT = 1_000_000
MIN_POINTS_POLYLINE = 2
MIN_POINTS_POLYGON = 3


def _ensure_coordinate_bounds(value: int) -> int:
    """坐标绝对值不得超过 COORD_LIMIT 毫米。"""
    if abs(value) > COORD_LIMIT:
        raise PydanticCustomError(
            "coordinate_out_of_range",
            f"坐标绝对值不得超过 {COORD_LIMIT} 毫米，收到 {value}",
        )
    return value


class PointModel(BaseModel):
    """毫米整数坐标点。"""

    model_config = ConfigDict(extra="forbid")

    x: StrictInt = Field(..., description="X 坐标（毫米，|x| <= 1,000,000）")
    y: StrictInt = Field(..., description="Y 坐标（毫米，|y| <= 1,000,000）")

    @field_validator("x", "y")
    @classmethod
    def _check_bounds(cls, value: int) -> int:
        return _ensure_coordinate_bounds(value)


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

    # 多边形几何检查（点结构有效时才检查，避免对脏数据重复报错）
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
        elif polygon_is_degenerate(raw):
            line.append(
                {
                    "type": PydanticCustomError(
                        "degenerate_polygon",
                        f"{shape_label}面积为零：有效顶点全部共线，隐式闭合边与其它边重叠，不得作为车辆轮廓",
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

    @field_validator("name")
    @classmethod
    def _check_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise PydanticCustomError(
                "blank_placement_name",
                "摆放位置名称不能为空白字符，必须包含有效名称",
            )
        return value


def _validate_placements_field(value: object, handler) -> list[Placement]:
    """校验摆放位置列表，并在嵌套字段错误时仍收集批次级重名错误。"""
    try:
        return handler(value)
    except ValidationError as exc:
        line_errors: list[dict] = []

        if isinstance(value, list):
            seen: dict[str, int] = {}
            for idx, item in enumerate(value):
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str):
                    continue
                first = seen.get(name)
                if first is not None:
                    line_errors.append(
                        {
                            "type": PydanticCustomError(
                                "duplicate_placement_name",
                                f"摆放位置名称重复：{name}（首次出现于第 {first} 个位置），名称必须唯一",
                            ),
                            "loc": (idx, "name"),
                        }
                    )
                else:
                    seen[name] = idx

            for error in exc.errors():
                loc = tuple(
                    part
                    for part in error.get("loc", ())
                    if part != "" and part != "placements"
                )
                line_errors.append(
                    {
                        "type": PydanticCustomError(
                            str(error["type"]),
                            str(error["msg"]),
                        ),
                        "loc": loc,
                    }
                )

        if not line_errors:
            raise
        raise ValidationError.from_exception_data(ClearanceSeriesRequest.__name__, line_errors) from exc


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

    @field_validator("placements", mode="wrap")
    @classmethod
    def _validate_placements_field(cls, value: object, handler) -> list[Placement]:
        return _validate_placements_field(value, handler)

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


class ProfilePoint(BaseModel):
    """单个测点：组内唯一名称 + 毫米整数坐标。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="测点名称，同一组内唯一")
    x: StrictInt = Field(..., description="X 坐标（毫米，|x| <= 1,000,000）")
    y: StrictInt = Field(..., description="Y 坐标（毫米，|y| <= 1,000,000）")

    @field_validator("name")
    @classmethod
    def _check_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise PydanticCustomError(
                "blank_point_name",
                "测点名称不能为空白字符，必须包含有效名称",
            )
        return value

    @field_validator("x", "y")
    @classmethod
    def _check_bounds(cls, value: int) -> int:
        return _ensure_coordinate_bounds(value)


def _validate_named_points_field(
    value: object, handler, *, field_name: str
) -> list[ProfilePoint]:
    """校验测点列表，并在嵌套字段错误时仍收集组内重名错误。"""
    try:
        return handler(value)
    except ValidationError as exc:
        line_errors: list[dict] = []

        if isinstance(value, list):
            seen: dict[str, int] = {}
            for idx, item in enumerate(value):
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str):
                    continue
                first = seen.get(name)
                if first is not None:
                    line_errors.append(
                        {
                            "type": PydanticCustomError(
                                "duplicate_point_name",
                                f"测点名称重复：{name}（首次出现于第 {first} 个测点），同组内名称必须唯一",
                            ),
                            "loc": (idx, "name"),
                        }
                    )
                else:
                    seen[name] = idx

            for error in exc.errors():
                loc = tuple(
                    part
                    for part in error.get("loc", ())
                    if part != "" and part != field_name
                )
                line_errors.append(
                    {
                        "type": PydanticCustomError(
                            str(error["type"]),
                            str(error["msg"]),
                        ),
                        "loc": loc,
                    }
                )

        if not line_errors:
            raise
        raise ValidationError.from_exception_data(ProfileCompareRequest.__name__, line_errors) from exc


class ProfileCompareRequest(BaseModel):
    """同一断面两期测点比对请求：两组测点按名称一一对应（数量、顺序完全一致）。"""

    model_config = ConfigDict(extra="forbid")

    baseline_points: list[ProfilePoint] = Field(
        ..., min_length=1, description="基准测点列表，名称组内唯一"
    )
    current_points: list[ProfilePoint] = Field(
        ...,
        min_length=1,
        description="本期测点列表，名称组内唯一，名称顺序须与基准测点完全一致",
    )
    reference_point: str = Field(
        ..., min_length=1, description="两组中共同存在的基准点名称，用于对齐仪器整体平移"
    )
    tolerance: StrictInt = Field(
        ...,
        ge=0,
        description="位移容差（毫米，非负整数）；未舍入位移 <= 容差 判为合格",
    )

    @field_validator("baseline_points", mode="wrap")
    @classmethod
    def _validate_baseline_points_field(cls, value: object, handler) -> list[ProfilePoint]:
        return _validate_named_points_field(value, handler, field_name="baseline_points")

    @field_validator("current_points", mode="wrap")
    @classmethod
    def _validate_current_points_field(cls, value: object, handler) -> list[ProfilePoint]:
        return _validate_named_points_field(value, handler, field_name="current_points")

    @model_validator(mode="after")
    def _validate_comparison(self) -> "ProfileCompareRequest":
        errors: list[dict] = []

        # 各组内名称唯一（定位到重复出现的后者）
        for field_name, points in (
            ("baseline_points", self.baseline_points),
            ("current_points", self.current_points),
        ):
            seen: dict[str, int] = {}
            for idx, point in enumerate(points):
                first = seen.get(point.name)
                if first is not None:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "duplicate_point_name",
                                f"测点名称重复：{point.name}（首次出现于第 {first} 个测点），同组内名称必须唯一",
                            ),
                            "loc": (field_name, idx, "name"),
                        }
                    )
                else:
                    seen[point.name] = idx

        # 两组名称序列必须完全一致（数量 + 顺序），定位到首个分歧
        if len(self.baseline_points) != len(self.current_points):
            errors.append(
                {
                    "type": PydanticCustomError(
                        "point_count_mismatch",
                        f"本期测点数量（{len(self.current_points)}）与基准测点数量"
                        f"（{len(self.baseline_points)}）不一致，两组测点数量与名称顺序必须完全对应",
                    ),
                    "loc": ("current_points",),
                }
            )
        else:
            for idx, (base, curr) in enumerate(
                zip(self.baseline_points, self.current_points)
            ):
                if base.name != curr.name:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "point_name_mismatch",
                                f"第 {idx} 个测点名称不一致：基准为 '{base.name}'，本期为 '{curr.name}'，"
                                "两组测点名称与顺序必须完全对应",
                            ),
                            "loc": ("current_points", idx, "name"),
                        }
                    )
                    break

        # 基准点必须同时存在于两组（基准点自身重复时重名错误已报告，不再重复检查）
        ref_in_baseline = [
            p for p in self.baseline_points if p.name == self.reference_point
        ]
        ref_in_current = [
            p for p in self.current_points if p.name == self.reference_point
        ]
        if not ref_in_baseline or not ref_in_current:
            if not ref_in_baseline and not ref_in_current:
                msg = f"基准点 '{self.reference_point}' 在基准测点与本期测点中均不存在"
            elif not ref_in_baseline:
                msg = f"基准点 '{self.reference_point}' 不存在于基准测点中"
            else:
                msg = f"基准点 '{self.reference_point}' 不存在于本期测点中"
            errors.append(
                {
                    "type": PydanticCustomError("reference_point_missing", msg),
                    "loc": ("reference_point",),
                }
            )
        elif len(ref_in_baseline) == 1 and len(ref_in_current) == 1:
            # 修正后坐标不得越过既有范围（±1,000,000 毫米）
            dx = ref_in_baseline[0].x - ref_in_current[0].x
            dy = ref_in_baseline[0].y - ref_in_current[0].y
            for idx, point in enumerate(self.current_points):
                cx = point.x + dx
                cy = point.y + dy
                if abs(cx) > COORD_LIMIT:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "corrected_coordinate_out_of_range",
                                f"测点 {point.name} 修正后 x 坐标为 {cx}，"
                                f"绝对值不得超过 {COORD_LIMIT} 毫米",
                            ),
                            "loc": ("current_points", idx, "x"),
                        }
                    )
                if abs(cy) > COORD_LIMIT:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "corrected_coordinate_out_of_range",
                                f"测点 {point.name} 修正后 y 坐标为 {cy}，"
                                f"绝对值不得超过 {COORD_LIMIT} 毫米",
                            ),
                            "loc": ("current_points", idx, "y"),
                        }
                    )

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


class CorrectionOut(BaseModel):
    """对齐修正量：本期测点整体平移 (dx, dy) 后与基准断面对齐。"""

    dx: int = Field(..., description="X 方向修正量（毫米）")
    dy: int = Field(..., description="Y 方向修正量（毫米）")


class ComparedPointOut(BaseModel):
    """单个测点的修正后坐标与位移。"""

    name: str
    x: int = Field(..., description="修正后 X 坐标（毫米）")
    y: int = Field(..., description="修正后 Y 坐标（毫米）")
    displacement_mm: float = Field(
        ..., description="与同名基准测点的欧氏位移（毫米，保留三位小数）"
    )


class ProfileCompareResponse(BaseModel):
    """断面变化比对报告：points 与输入本期测点按顺序一一对应。"""

    correction: CorrectionOut
    points: list[ComparedPointOut]
    max_displacement_name: str = Field(
        ..., description="位移最大的测点名称；并列时取输入顺序靠前者"
    )
    max_displacement_mm: float = Field(
        ..., description="最大位移（毫米，保留三位小数）"
    )
    exceeded_names: list[str] = Field(
        ..., description="位移超过容差的测点名称，按输入顺序排列"
    )
    all_passed: bool = Field(..., description="全部测点位移均未超过容差时为 true")
