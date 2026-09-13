"""断面两期测点比对：基准点对齐 + 逐点位移计算（纯 Python，不依赖 FastAPI）。

契约（调用方已在模型层完成校验）：
- 两组测点名称序列完全一致（数量、顺序、名称逐一对应）且组内唯一；
- 基准点名称同时唯一存在于两组；
- 容差为非负整数（毫米）；
- 修正后坐标不越过 ±1,000,000 毫米；
- 两组均至少包含一个测点。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import round_three

# 测点：(名称, x 毫米, y 毫米)
NamedPoint = tuple[str, int, int]


@dataclass(frozen=True)
class ComparedPoint:
    """单个测点的比对结果：修正后坐标 + 与同名基准测点的位移。"""

    name: str
    x: int
    y: int
    displacement_mm: float


@dataclass(frozen=True)
class ComparisonResult:
    """整断面比对结论。"""

    dx: int
    dy: int
    points: list[ComparedPoint]
    max_displacement_name: str
    max_displacement_mm: float
    exceeded_names: list[str]
    all_passed: bool


def compare_profiles(
    baseline_points: list[NamedPoint],
    current_points: list[NamedPoint],
    reference_name: str,
    tolerance: int,
) -> ComparisonResult:
    """以两组基准点的坐标差平移全部本期测点，再逐点计算与基准测点的欧氏位移。

    - 修正量 (dx, dy) = 基准组基准点坐标 - 本期组基准点坐标；
    - 容差判定使用未舍入的双精度位移：位移 > 容差 视为超限（恰好等于容差判合格）；
    - 输出位移四舍五入到三位小数（ROUND_HALF_UP）；
    - 最大位移并列时保留输入顺序靠前的测点（严格更大才替换）。
    """
    base_by_name = {name: (x, y) for name, x, y in baseline_points}
    curr_by_name = {name: (x, y) for name, x, y in current_points}
    base_ref = base_by_name[reference_name]
    curr_ref = curr_by_name[reference_name]
    dx = base_ref[0] - curr_ref[0]
    dy = base_ref[1] - curr_ref[1]

    points: list[ComparedPoint] = []
    exceeded: list[str] = []
    max_name = ""
    max_displacement = -1.0  # 位移恒为非负，首个测点必然替换该哨兵

    for name, x, y in current_points:
        cx = x + dx
        cy = y + dy
        bx, by = base_by_name[name]
        displacement = math.hypot(cx - bx, cy - by)
        points.append(
            ComparedPoint(
                name=name,
                x=cx,
                y=cy,
                displacement_mm=round_three(displacement),
            )
        )
        if displacement > float(tolerance):
            exceeded.append(name)
        if displacement > max_displacement:
            max_displacement = displacement
            max_name = name

    return ComparisonResult(
        dx=dx,
        dy=dy,
        points=points,
        max_displacement_name=max_name,
        max_displacement_mm=round_three(max_displacement),
        exceeded_names=exceeded,
        all_passed=not exceeded,
    )
