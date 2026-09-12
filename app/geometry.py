"""平面几何原语（双精度，纯 Python 实现，不依赖任何几何库）。

坐标以毫米整数输入，内部统一转为 float（IEEE-754 双精度）参与运算。

约定：
- 折线/多边形用点列表 ``[(x, y), ...]`` 表示；
- 线段为点对 ``((x1, y1), (x2, y2))``；
- 点索引 ``i`` 对应的线段起点为第 ``i`` 个点。
"""

from __future__ import annotations

import math

# 并列距离判定阈值：距离差不超过该值视为并列
DISTANCE_TIE_EPS = 1e-9

Point = tuple[float, float]
Segment = tuple[Point, Point]


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _cross(u: Point, v: Point) -> float:
    return u[0] * v[1] - u[1] * v[0]


def _dot(u: Point, v: Point) -> float:
    return u[0] * v[0] + u[1] * v[1]


def _orientation(a: Point, b: Point, c: Point) -> float:
    """有向面积 (b-a) x (c-a)；正值表示 c 在有向线段 a->b 左侧。"""
    return _cross(_sub(b, a), _sub(c, a))


def _on_segment_collinear(p: Point, a: Point, b: Point) -> bool:
    """已知 p 与 a,b 共线时，判断 p 是否落在线段 ab 的闭包上（含端点）。"""
    return (
        min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def segments_intersect(s1: Segment, s2: Segment) -> bool:
    """判断两条线段是否相交或接触（端点落在对方线段上、部分重叠均算相交）。

    规则：
    1. 严格跨越（两端点分列两侧，符号互异）为普通相交；
    2. 退化情形（共线重叠 / 端点接触）由共线点闭包含盖判定。

    注意：同起点的相邻线段会被判为相交，多边形自交检查必须跳过相邻边。
    """
    a, b = s1
    c, d = s2

    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)

    if o1 * o2 < 0.0 and o3 * o4 < 0.0:
        return True

    # 接触 / 共线重叠
    if o1 == 0.0 and _on_segment_collinear(c, a, b):
        return True
    if o2 == 0.0 and _on_segment_collinear(d, a, b):
        return True
    if o3 == 0.0 and _on_segment_collinear(a, c, d):
        return True
    if o4 == 0.0 and _on_segment_collinear(b, c, d):
        return True

    return False


def point_segment_distance(p: Point, seg: Segment) -> float:
    """点到线段的欧氏距离，垂足落在线段外时取到较近端点的距离。"""
    a, b = seg
    ab = _sub(b, a)
    ap = _sub(p, a)
    length_sq = _dot(ab, ab)
    # 调用方已保证线段端点不同；此处防御零长线段
    if length_sq == 0.0:
        return math.hypot(ap[0], ap[1])

    t = _dot(ap, ab) / length_sq
    if t <= 0.0:
        nearest = a
    elif t >= 1.0:
        nearest = b
    else:
        nearest = (a[0] + t * ab[0], a[1] + t * ab[1])

    d = _sub(p, nearest)
    return math.hypot(d[0], d[1])


def segment_distance(s1: Segment, s2: Segment) -> float:
    """两条线段间的距离：相交或接触为 0，否则取四个端点到对方线段距离的最小值。"""
    if segments_intersect(s1, s2):
        return 0.0
    a, b = s1
    c, d = s2
    return min(
        point_segment_distance(a, s2),
        point_segment_distance(b, s2),
        point_segment_distance(c, s1),
        point_segment_distance(d, s1),
    )


def iter_segments(points: list[Point], close: bool) -> "object":
    """按顺序产出线段 (index, segment)。

    ``close=True`` 时追加首尾隐式闭合边，闭合边索引为 ``len(points) - 1``。
    """
    n = len(points)
    for i in range(n - 1):
        yield i, (points[i], points[i + 1])
    if close and n >= 2:
        yield n - 1, (points[n - 1], points[0])


def polygon_self_intersects(points: list[Point]) -> tuple[int, int] | None:
    """检查隐式闭合多边形是否自交（含非相邻边接触）。

    返回自交的一对边的起点索引；无自交返回 None。
    相邻边（含末边与首边）共享端点属正常，不视为自交。
    返回的索引对按 (i, j) 升序，保证结果稳定。
    """
    edges = list(iter_segments(points, close=True))
    m = len(edges)
    for ki in range(m):
        i, s_i = edges[ki]
        for kj in range(ki + 1, m):
            j, s_j = edges[kj]
            adjacent = (ki + 1 == kj) or (ki == 0 and kj == m - 1)
            if adjacent:
                continue
            if segments_intersect(s_i, s_j):
                return (i, j)
    return None


def minimum_segment_pair(
    tunnel_points: list[Point],
    polygon_points: list[Point],
) -> tuple[float, int, int]:
    """计算隧道折线与闭合多边形两组线段之间的全局最小欧氏距离。

    返回 ``(最小距离, 隧道线段起点索引, 限界边起点索引)``。

    并列规则：两个最小距离之差不超过 1e-9 时视为并列，依次取
    隧道线段起点索引、限界边起点索引较小者。按双层循环升序遍历，
    仅当新距离严格更小（差值大于 1e-9）才更新，天然保留较小索引对。
    """
    tunnel_edges = list(iter_segments(tunnel_points, close=False))
    polygon_edges = list(iter_segments(polygon_points, close=True))

    best_distance = math.inf
    best_ti = 0
    best_pi = 0

    for ti, s_t in tunnel_edges:
        for pi, s_p in polygon_edges:
            d = segment_distance(s_t, s_p)
            if d + DISTANCE_TIE_EPS < best_distance:
                best_distance = d
                best_ti = ti
                best_pi = pi

    return best_distance, best_ti, best_pi


def round_three(value: float) -> float:
    """四舍五入到小数点后三位（ROUND_HALF_UP，避免 Python 银行家舍入）。"""
    from decimal import ROUND_HALF_UP, Decimal

    return float(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
