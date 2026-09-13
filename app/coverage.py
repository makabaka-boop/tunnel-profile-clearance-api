"""控制点对测量折线的覆盖分析：逐点计算点到折线的最短距离与覆盖结论。

纯 Python 实现，不依赖 FastAPI；点到线段距离复用 geometry 基础能力。

契约（调用方已在模型层完成校验）：
- 测量折线按顺序连接且不闭合，至少 2 个点且相邻点不重合；
- 控制点名称非空白且组内唯一；
- 控制点列表非空；
- 覆盖半径为非负整数（毫米）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import DISTANCE_TIE_EPS, iter_segments, point_segment_distance, round_three

# 控制点：(名称, x 毫米, y 毫米)
NamedPoint = tuple[str, float, float]


@dataclass(frozen=True)
class PointCoverage:
    """单个控制点的覆盖结论：到折线的最短距离与最近线段起点索引。"""

    name: str
    distance_mm: float
    nearest_segment_start_index: int
    covered: bool


@dataclass(frozen=True)
class CoverageReport:
    """整批控制点覆盖结论：points 与输入控制点按顺序一一对应。"""

    points: list[PointCoverage]
    first_uncovered_name: str | None
    all_covered: bool


def nearest_polyline_segment(polyline: list[tuple[float, float]], p: tuple[float, float]) -> tuple[float, int]:
    """计算点 p 到不闭合折线各线段的最短欧氏距离及最近线段起点索引。

    并列规则：两条线段距离之差不超过 1e-9 时视为并列，取起点索引较小者。
    按线段起点索引升序遍历，仅当新距离严格更小（差值大于 1e-9）才更新，
    天然保留较小索引，与 minimum_segment_pair 的选边语义一致。
    """
    best_distance = float("inf")
    best_index = 0
    for index, segment in iter_segments(polyline, close=False):
        distance = point_segment_distance(p, segment)
        if distance + DISTANCE_TIE_EPS < best_distance:
            best_distance = distance
            best_index = index
    return best_distance, best_index


def analyze_coverage(
    polyline: list[tuple[float, float]],
    control_points: list[NamedPoint],
    radius: int,
) -> CoverageReport:
    """逐控制点计算到测量折线的最短距离，并按覆盖半径判定是否有效覆盖。

    - 覆盖判定使用**未舍入**的双精度距离：距离 <= 半径判为已覆盖
      （恰好落在半径边界上也算覆盖）；
    - 输出距离四舍五入到三位小数（ROUND_HALF_UP）；
    - 结果与输入控制点顺序一一对应，单个控制点未覆盖不中断后续计算；
    - first_uncovered_name 取输入顺序中首个未覆盖控制点的名称，全部覆盖时为 None。
    """
    points: list[PointCoverage] = []
    first_uncovered_name: str | None = None

    for name, x, y in control_points:
        distance, segment_index = nearest_polyline_segment(polyline, (x, y))
        covered = distance <= float(radius)
        points.append(
            PointCoverage(
                name=name,
                distance_mm=round_three(distance),
                nearest_segment_start_index=segment_index,
                covered=covered,
            )
        )
        if not covered and first_uncovered_name is None:
            first_uncovered_name = name

    return CoverageReport(
        points=points,
        first_uncovered_name=first_uncovered_name,
        all_covered=first_uncovered_name is None,
    )
