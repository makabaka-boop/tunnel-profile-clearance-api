"""请求 / 响应的 Pydantic 模型与字段级几何校验。"""

from __future__ import annotations

import unicodedata

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


def _has_valid_name(value: str) -> bool:
    """名称去掉空白与控制字符后必须仍有有效内容。

    ``str.strip()`` 只去除空白（空格/制表符/换行等），NUL(\\x00) 等
    纯控制字符不会被去除；因此额外剥离 Unicode 控制字符（类别 Cc）后再判空。
    """
    stripped = "".join(ch for ch in value if unicodedata.category(ch) != "Cc").strip()
    return bool(stripped)


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
        if not _has_valid_name(value):
            raise PydanticCustomError(
                "blank_placement_name",
                "摆放位置名称不能为空白或控制字符，必须包含有效名称",
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
        if not _has_valid_name(value):
            raise PydanticCustomError(
                "blank_point_name",
                "测点名称不能为空白或控制字符，必须包含有效名称",
            )
        return value

    @field_validator("x", "y")
    @classmethod
    def _check_bounds(cls, value: int) -> int:
        return _ensure_coordinate_bounds(value)


class ControlPoint(BaseModel):
    """单个控制点：组内唯一名称 + 毫米整数坐标（拱顶 / 侧墙 / 设备邻近点等）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="控制点名称，同一请求内唯一")
    x: StrictInt = Field(..., description="X 坐标（毫米，|x| <= 1,000,000）")
    y: StrictInt = Field(..., description="Y 坐标（毫米，|y| <= 1,000,000）")

    @field_validator("name")
    @classmethod
    def _check_name_not_blank(cls, value: str) -> str:
        if not _has_valid_name(value):
            raise PydanticCustomError(
                "blank_control_point_name",
                "控制点名称不能为空白或控制字符，必须包含有效名称",
            )
        return value

    @field_validator("x", "y")
    @classmethod
    def _check_bounds(cls, value: int) -> int:
        return _ensure_coordinate_bounds(value)


def _validate_named_points_field(
    value: object,
    handler,
    *,
    field_name: str,
    owner_name: str,
    name_label: str = "测点",
    duplicate_type: str = "duplicate_point_name",
) -> list[ProfilePoint]:
    """校验命名点列表，并在嵌套字段错误时仍收集组内重名错误。

    name_label 用于组内重名错误消息中的人类可读名称（测点 / 控制点）；
    duplicate_type 为重名错误类型（duplicate_point_name /
    duplicate_control_point_name）。
    """
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
                                duplicate_type,
                                f"{name_label}名称重复：{name}（首次出现于第 {first} 个{name_label}），"
                                "同组内名称必须唯一",
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
        raise ValidationError.from_exception_data(owner_name, line_errors) from exc


def _profile_pair_errors(
    baseline_points: list[ProfilePoint],
    current_points: list[ProfilePoint],
    reference_point: str,
) -> list[dict]:
    """两期测点共有校验：组内重名、名称序列一致、基准点存在、修正后不越界。

    供 ProfileCompareRequest 与 ClearanceImpactRequest 复用，保证两接口语义一致。
    """
    errors: list[dict] = []

    # 各组内名称唯一（定位到重复出现的后者）
    for field_name, points in (
        ("baseline_points", baseline_points),
        ("current_points", current_points),
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
    if len(baseline_points) != len(current_points):
        errors.append(
            {
                "type": PydanticCustomError(
                    "point_count_mismatch",
                    f"本期测点数量（{len(current_points)}）与基准测点数量"
                    f"（{len(baseline_points)}）不一致，两组测点数量与名称顺序必须完全对应",
                ),
                "loc": ("current_points",),
            }
        )
    else:
        for idx, (base, curr) in enumerate(zip(baseline_points, current_points)):
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
    ref_in_baseline = [p for p in baseline_points if p.name == reference_point]
    ref_in_current = [p for p in current_points if p.name == reference_point]
    if not ref_in_baseline or not ref_in_current:
        if not ref_in_baseline and not ref_in_current:
            msg = f"基准点 '{reference_point}' 在基准测点与本期测点中均不存在"
        elif not ref_in_baseline:
            msg = f"基准点 '{reference_point}' 不存在于基准测点中"
        else:
            msg = f"基准点 '{reference_point}' 不存在于本期测点中"
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
        for idx, point in enumerate(current_points):
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

    return errors


def _dict_list(value: object) -> list[dict] | None:
    """字段值为 dict 列表时原样返回，否则返回 None（类型错误由字段级校验报告）。"""
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    return None


def _valid_int(value: object) -> int | None:
    """提取严格整数（排除布尔值冒充）；非整数返回 None（由字段级校验报告）。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _raw_profile_pair_errors(raw: dict, *, min_points: int) -> list[dict]:
    """从原始输入计算两期测点的联合校验错误。

    字段级 / 嵌套模型级校验失败时，``mode="after"`` 模型校验不会运行，
    联合错误（名称序列、基准点、修正后越界、相邻重合、点数下限）会被遗漏。
    本函数直接基于原始 dict 计算这些错误，供模型级 ``wrap`` 校验器在
    字段构造失败时一并返回；仅对结构合法的部分做检查，避免错误噪音：

    - 名称序列比较只使用非空白/控制字符的有效名称，非法名称由字段级错误定位；
    - 坐标类检查只使用在界严格整数坐标；
    - 每组点数下限由列表长度直接判断（即使组内含脏点）。
    """
    errors: list[dict] = []

    baseline_raw = _dict_list(raw.get("baseline_points"))
    current_raw = _dict_list(raw.get("current_points"))
    reference = raw.get("reference_point")

    if baseline_raw is None or current_raw is None:
        return errors

    # 列表长度下限：即使组内含字段级脏点，也要同时给出后续联合错误
    for field_name, points in (
        ("baseline_points", baseline_raw),
        ("current_points", current_raw),
    ):
        if len(points) < min_points:
            errors.append(
                {
                    "type": PydanticCustomError(
                        "too_short",
                        f"列表至少需要 {min_points} 个项，实际有 {len(points)} 个",
                    ),
                    "loc": (field_name,),
                }
            )

    # 两组名称序列必须完全一致（数量 + 顺序），定位到首个分歧。
    # 名称非法（非字符串/空白/控制字符）时由字段级校验定位，该项不参与比对，
    # 避免在联合错误消息里夹带非法名称
    def _valid_name(item: dict) -> str | None:
        name = item.get("name")
        if isinstance(name, str) and _has_valid_name(name):
            return name
        return None

    if len(baseline_raw) == len(current_raw):
        for idx, (base_item, curr_item) in enumerate(zip(baseline_raw, current_raw)):
            bn = _valid_name(base_item)
            cn = _valid_name(curr_item)
            if bn is not None and cn is not None and bn != cn:
                errors.append(
                    {
                        "type": PydanticCustomError(
                            "point_name_mismatch",
                            f"第 {idx} 个测点名称不一致：基准为 '{bn}'，本期为 '{cn}'，"
                            "两组测点名称与顺序必须完全对应",
                        ),
                        "loc": ("current_points", idx, "name"),
                    }
                )
                break
    else:
        errors.append(
            {
                "type": PydanticCustomError(
                    "point_count_mismatch",
                    f"本期测点数量（{len(current_raw)}）与基准测点数量"
                    f"（{len(baseline_raw)}）不一致，两组测点数量与名称顺序必须完全对应",
                ),
                "loc": ("current_points",),
            }
        )

    # 相邻测点不得重合（零长线段无法构成折线）：坐标为在界整数即可判定，
    # 与同项其它字段是否合法无关，故脏点并存时仍能定位无效折线
    for field_name, label, points in (
        ("baseline_points", "基准测点", baseline_raw),
        ("current_points", "本期测点", current_raw),
    ):
        for k in range(1, len(points)):
            ax, ay = _valid_int(points[k - 1].get("x")), _valid_int(points[k - 1].get("y"))
            bx, by = _valid_int(points[k].get("x")), _valid_int(points[k].get("y"))
            if (
                ax is not None
                and ay is not None
                and bx is not None
                and by is not None
                and abs(ax) <= COORD_LIMIT
                and abs(ay) <= COORD_LIMIT
                and abs(bx) <= COORD_LIMIT
                and abs(by) <= COORD_LIMIT
                and ax == bx
                and ay == by
            ):
                errors.append(
                    {
                        "type": PydanticCustomError(
                            "duplicate_adjacent_point",
                            f"{label}第 {k} 个测点与前一点重合，作为折线的相邻测点不得相同",
                        ),
                        "loc": (field_name, k),
                    }
                )

    if not isinstance(reference, str) or not _has_valid_name(reference):
        return errors

    # 基准点必须同时存在于两组（只按有效字符串名称匹配；非法名称项由字段级错误定位）
    ref_in_baseline = [
        (idx, item)
        for idx, item in enumerate(baseline_raw)
        if _valid_name(item) == reference
    ]
    ref_in_current = [
        (idx, item)
        for idx, item in enumerate(current_raw)
        if _valid_name(item) == reference
    ]
    if not ref_in_baseline or not ref_in_current:
        if not ref_in_baseline and not ref_in_current:
            msg = f"基准点 '{reference}' 在基准测点与本期测点中均不存在"
        elif not ref_in_baseline:
            msg = f"基准点 '{reference}' 不存在于基准测点中"
        else:
            msg = f"基准点 '{reference}' 不存在于本期测点中"
        errors.append(
            {
                "type": PydanticCustomError("reference_point_missing", msg),
                "loc": ("reference_point",),
            }
        )
    elif len(ref_in_baseline) == 1 and len(ref_in_current) == 1:
        # 修正后坐标不得越过既有范围（±1,000,000 毫米）：
        # 基准点与各测点坐标均为在界整数时才计算，避免对脏数据重复报错
        _, base_ref_item = ref_in_baseline[0]
        _, curr_ref_item = ref_in_current[0]
        brx = _valid_int(base_ref_item.get("x"))
        bry = _valid_int(base_ref_item.get("y"))
        crx = _valid_int(curr_ref_item.get("x"))
        cry = _valid_int(curr_ref_item.get("y"))
        if None not in (brx, bry, crx, cry):
            dx, dy = brx - crx, bry - cry
            for idx, item in enumerate(current_raw):
                cx, cy = _valid_int(item.get("x")), _valid_int(item.get("y"))
                if cx is None or cy is None:
                    continue
                nx, ny = cx + dx, cy + dy
                name = _valid_name(item)
                label = name if name is not None else f"第 {idx} 个测点"
                if abs(nx) > COORD_LIMIT:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "corrected_coordinate_out_of_range",
                                f"测点 {label} 修正后 x 坐标为 {nx}，"
                                f"绝对值不得超过 {COORD_LIMIT} 毫米",
                            ),
                            "loc": ("current_points", idx, "x"),
                        }
                    )
                if abs(ny) > COORD_LIMIT:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "corrected_coordinate_out_of_range",
                                f"测点 {label} 修正后 y 坐标为 {ny}，"
                                f"绝对值不得超过 {COORD_LIMIT} 毫米",
                            ),
                            "loc": ("current_points", idx, "y"),
                        }
                    )

    return errors


def _run_model_with_raw_joint_errors(
    cls: type, value: object, handler, *, min_points: int
):
    """模型级 wrap 校验：字段级失败时仍从原始输入补算联合校验错误。

    正常路径（全部字段构造成功）交给原 ``mode="after"`` 校验器，其语义
    与既有行为完全一致；字段级 / 嵌套模型级失败时，handler 抛出
    ValidationError，本函数重建错误列表并追加基于原始数据的联合错误，
    按 (类型, loc) 去重：同一字段位置的同类错误只保留首个字段级错误，
    避免与原校验器或字段 wrap 校验器重复。
    """
    try:
        return handler(value)
    except ValidationError as exc:
        line_errors: list[dict] = []
        seen_keys: set[tuple] = set()

        def _add(error_type, msg: str, loc: tuple) -> None:
            # 同一 loc 上的同类错误只保留首个：字段级错误先入列并保留其
            # 标准消息（如 too_short），原始补算的重复联合错误不再追加
            key = (str(error_type), loc)
            if key not in seen_keys:
                seen_keys.add(key)
                line_errors.append(
                    {
                        "type": PydanticCustomError(str(error_type), str(msg)),
                        "loc": loc,
                    }
                )

        for error in exc.errors():
            loc = tuple(part for part in error.get("loc", ()) if part != "")
            _add(error["type"], error["msg"], loc)

        if isinstance(value, dict):
            for error in _raw_profile_pair_errors(value, min_points=min_points):
                custom_error: PydanticCustomError = error["type"]
                loc = tuple(error["loc"])
                _add(custom_error.type, custom_error.message, loc)

        raise ValidationError.from_exception_data(cls.__name__, line_errors) from exc


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
        return _validate_named_points_field(
            value, handler, field_name="baseline_points", owner_name=cls.__name__
        )

    @field_validator("current_points", mode="wrap")
    @classmethod
    def _validate_current_points_field(cls, value: object, handler) -> list[ProfilePoint]:
        return _validate_named_points_field(
            value, handler, field_name="current_points", owner_name=cls.__name__
        )

    @field_validator("reference_point")
    @classmethod
    def _check_reference_point_not_blank(cls, value: str) -> str:
        if not _has_valid_name(value):
            raise PydanticCustomError(
                "blank_reference_point",
                "基准点名称不能为空白或控制字符，必须包含有效名称",
            )
        return value

    @model_validator(mode="wrap")
    @classmethod
    def _merge_field_and_joint_errors(cls, data, handler):
        return _run_model_with_raw_joint_errors(
            cls, data, handler, min_points=1
        )

    @model_validator(mode="after")
    def _validate_comparison(self) -> "ProfileCompareRequest":
        errors = _profile_pair_errors(
            self.baseline_points, self.current_points, self.reference_point
        )
        if errors:
            raise ValidationError.from_exception_data(self.__class__.__name__, errors)
        return self


class ClearanceImpactRequest(BaseModel):
    """两期测点的限界影响复核请求：两期测点序列分别作为不闭合隧道折线。"""

    model_config = ConfigDict(extra="forbid")

    baseline_points: list[ProfilePoint] = Field(
        ..., min_length=2, description="基准测点列表（至少 2 个，按顺序构成不闭合折线），名称组内唯一"
    )
    current_points: list[ProfilePoint] = Field(
        ...,
        min_length=2,
        description="本期测点列表（至少 2 个），名称与顺序须与基准测点完全一致",
    )
    reference_point: str = Field(
        ..., min_length=1, description="两组中共同存在的基准点名称，用于对齐仪器整体平移"
    )
    vehicle_polygon: VehicleGauge
    required_clearance: StrictInt = Field(
        ...,
        ge=-COORD_LIMIT,
        le=COORD_LIMIT,
        description="要求净距（毫米，整数，|required_clearance| <= 1,000,000）",
    )

    @field_validator("baseline_points", mode="wrap")
    @classmethod
    def _validate_baseline_points_field(cls, value: object, handler) -> list[ProfilePoint]:
        return _validate_named_points_field(
            value, handler, field_name="baseline_points", owner_name=cls.__name__
        )

    @field_validator("current_points", mode="wrap")
    @classmethod
    def _validate_current_points_field(cls, value: object, handler) -> list[ProfilePoint]:
        return _validate_named_points_field(
            value, handler, field_name="current_points", owner_name=cls.__name__
        )

    @field_validator("reference_point")
    @classmethod
    def _check_reference_point_not_blank(cls, value: str) -> str:
        if not _has_valid_name(value):
            raise PydanticCustomError(
                "blank_reference_point",
                "基准点名称不能为空白或控制字符，必须包含有效名称",
            )
        return value

    @model_validator(mode="wrap")
    @classmethod
    def _merge_field_and_joint_errors(cls, data, handler):
        return _run_model_with_raw_joint_errors(
            cls, data, handler, min_points=MIN_POINTS_POLYLINE
        )

    @model_validator(mode="after")
    def _validate_impact(self) -> "ClearanceImpactRequest":
        errors = _profile_pair_errors(
            self.baseline_points, self.current_points, self.reference_point
        )

        # 相邻测点不得重合：两期序列分别作为不闭合折线进入净距计算，
        # 零长线段没有几何意义（平移修正不改变重合关系，修正前后判定一致）
        for field_name, label, points in (
            ("baseline_points", "基准测点", self.baseline_points),
            ("current_points", "本期测点", self.current_points),
        ):
            for k in range(1, len(points)):
                if points[k].x == points[k - 1].x and points[k].y == points[k - 1].y:
                    errors.append(
                        {
                            "type": PydanticCustomError(
                                "duplicate_adjacent_point",
                                f"{label}第 {k} 个测点与前一点重合，作为折线的相邻测点不得相同",
                            ),
                            "loc": (field_name, k),
                        }
                    )

        if errors:
            raise ValidationError.from_exception_data(self.__class__.__name__, errors)
        return self


class CoverageRequest(BaseModel):
    """控制点覆盖复核请求：测量折线（按序连接、不闭合）+ 命名控制点列表 + 覆盖半径。"""

    model_config = ConfigDict(extra="forbid")

    measured_polyline: TunnelProfile
    control_points: list[ControlPoint] = Field(
        ...,
        min_length=1,
        description="设计指定的控制点列表（至少 1 个），名称非空白且同一请求内唯一，按输入顺序逐一复核",
    )
    coverage_radius: StrictInt = Field(
        ...,
        ge=0,
        description="覆盖半径（毫米，非负整数）；未舍入最短距离 <= 半径判为已覆盖",
    )

    @field_validator("control_points", mode="wrap")
    @classmethod
    def _validate_control_points_field(cls, value: object, handler) -> list[ControlPoint]:
        return _validate_named_points_field(
            value,
            handler,
            field_name="control_points",
            owner_name=cls.__name__,
            name_label="控制点",
            duplicate_type="duplicate_control_point_name",
        )

    @model_validator(mode="after")
    def _validate_control_points(self) -> "CoverageRequest":
        # 名称在同一请求内唯一（定位到重复出现的后者）
        errors: list[dict] = []
        seen: dict[str, int] = {}
        for idx, point in enumerate(self.control_points):
            first = seen.get(point.name)
            if first is not None:
                errors.append(
                    {
                        "type": PydanticCustomError(
                            "duplicate_control_point_name",
                            f"控制点名称重复：{point.name}（首次出现于第 {first} 个控制点），"
                            "同组内名称必须唯一",
                        ),
                        "loc": ("control_points", idx, "name"),
                    }
                )
            else:
                seen[point.name] = idx

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


class ClearanceImpactResponse(BaseModel):
    """两期限界影响报告：基准期与本期（修正后）各自的完整净距结论 + 净距变化。"""

    baseline: ClearanceResponse = Field(..., description="基准期测点折线的净距结论")
    current: ClearanceResponse = Field(..., description="本期测点修正后折线的净距结论")
    clearance_change_mm: float = Field(
        ...,
        description="本期最小净距减基准期最小净距（毫米，未舍入差值四舍五入到三位小数）",
    )
    became_noncompliant: bool = Field(
        ..., description="基准期合格而本期不合格（由合格转为不合格）时为 true"
    )


class PointCoverageOut(BaseModel):
    """单个控制点的覆盖结论，顺序与输入控制点一一对应。"""

    name: str = Field(..., description="对应输入控制点的名称")
    distance_mm: float = Field(
        ..., description="控制点到测量折线的最短距离（毫米，保留三位小数）"
    )
    nearest_segment_start_index: int = Field(
        ...,
        description="最近线段的起点索引（折线点序列下标）；距离差不超过 1e-9 的并列取较小者",
    )
    covered: bool = Field(
        ..., description="未舍入最短距离 <= 覆盖半径时为 true（恰好落在半径边界上也算覆盖）"
    )


class CoverageResponse(BaseModel):
    """控制点覆盖复核报告：points 与输入控制点按顺序一一对应。"""

    points: list[PointCoverageOut]
    first_uncovered_name: str | None = Field(
        ..., description="首个未覆盖控制点的名称；全部覆盖时为 null"
    )
    all_covered: bool = Field(..., description="全部控制点均被有效覆盖时为 true")
