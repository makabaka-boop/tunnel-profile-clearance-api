"""几何原语边界测试。"""

import math

import pytest

from app.geometry import (
    DISTANCE_TIE_EPS,
    iter_segments,
    minimum_segment_pair,
    point_segment_distance,
    polygon_is_degenerate,
    polygon_self_intersects,
    round_three,
    segment_distance,
    segments_intersect,
)

P = tuple[float, float]


def pt(x: float, y: float) -> P:
    return (x, y)


# ---------- 线段相交 ----------

@pytest.mark.parametrize(
    "s1,s2",
    [
        # 普通交叉（X 形）
        (((0.0, 0.0), (10.0, 10.0)), ((0.0, 10.0), (10.0, 0.0))),
        # T 形：端点落在对方线段内部
        (((0.0, 0.0), (10.0, 0.0)), ((5.0, -5.0), (5.0, 0.0))),
        # 端点相接
        (((0.0, 0.0), (10.0, 0.0)), ((10.0, 0.0), (10.0, 10.0))),
        # 共线部分重叠
        (((0.0, 0.0), (10.0, 0.0)), ((5.0, 0.0), (15.0, 0.0))),
        # 共线包含
        (((0.0, 0.0), (10.0, 0.0)), ((2.0, 0.0), (4.0, 0.0))),
        # 共线仅端点接触
        (((0.0, 0.0), (10.0, 0.0)), ((10.0, 0.0), (20.0, 0.0))),
        # 完全重合
        (((0.0, 0.0), (10.0, 10.0)), ((0.0, 0.0), (10.0, 10.0))),
    ],
)
def test_segments_intersect_true(s1, s2):
    assert segments_intersect(s1, s2)
    assert segments_intersect(s2, s1)
    assert segment_distance(s1, s2) == 0.0


@pytest.mark.parametrize(
    "s1,s2,expected",
    [
        # 平行水平线段，竖直间距 3
        (((0.0, 0.0), (10.0, 0.0)), ((0.0, 3.0), (10.0, 3.0)), 3.0),
        # 上方水平线段端点(3,4) 投影到 x 轴为 (3,0)，距离 4
        (((0.0, 0.0), (3.0, 0.0)), ((3.0, 4.0), (8.0, 4.0)), 4.0),
        # 点到线段垂足在线段内：点(5,4) 到水平线段
        (((0.0, 0.0), (10.0, 0.0)), ((5.0, 4.0), (6.0, 4.0)), 4.0),
        # 交叉的斜线若不相交：(0,10)->(2,12) 远离对角线 y=x
        (((0.0, 0.0), (10.0, 10.0)), ((0.0, 10.0), (2.0, 12.0)), math.sqrt(50.0)),
    ],
)
def test_segment_distance_values(s1, s2, expected):
    assert not segments_intersect(s1, s2)
    assert segment_distance(s1, s2) == pytest.approx(expected, rel=1e-12)


def test_not_intersect_touch_corner():
    # 近乎接触但不接触：缝隙 1e-12 仍应判为不相交且距离非零
    s1 = ((0.0, 0.0), (10.0, 0.0))
    s2 = ((5.0, 1e-12), (5.0, 5.0))
    assert not segments_intersect(s1, s2)
    assert segment_distance(s1, s2) == pytest.approx(1e-12, abs=1e-20)


# ---------- 点到线段 ----------

def test_point_segment_distance_projection_inside():
    assert point_segment_distance(pt(5, 4), (pt(0, 0), pt(10, 0))) == pytest.approx(4.0)
    # 斜线 y = x，点 (0,10) 垂足 (5,5)，距离 sqrt(50)
    assert point_segment_distance(pt(0, 10), (pt(0, 0), pt(10, 10))) == pytest.approx(
        math.sqrt(50)
    )


def test_point_segment_distance_behind_start():
    # 垂足在起点之前 -> 到端点距离
    assert point_segment_distance(pt(-3, -4), (pt(0, 0), pt(10, 0))) == pytest.approx(5.0)


def test_point_segment_distance_past_end():
    # 垂足在终点之后 -> 到端点距离
    assert point_segment_distance(pt(13, 4), (pt(0, 0), pt(10, 0))) == pytest.approx(5.0)


def test_point_at_segment_endpoints():
    seg = (pt(0, 0), pt(10, 0))
    assert point_segment_distance(pt(0, 0), seg) == 0.0
    assert point_segment_distance(pt(10, 0), seg) == 0.0


# ---------- 隐式闭合 / 自交 ----------

def test_iter_segments_open_vs_closed():
    pts = [pt(0, 0), pt(1, 0), pt(1, 1)]
    open_edges = list(iter_segments(pts, close=False))
    assert [i for i, _ in open_edges] == [0, 1]

    closed = list(iter_segments(pts, close=True))
    assert [i for i, _ in closed] == [0, 1, 2]
    assert closed[2][1] == (pt(1, 1), pt(0, 0))  # 闭合边索引为 n-1


def test_triangle_not_self_intersecting():
    tri = [pt(0, 0), pt(1000, 0), pt(0, 1000)]
    assert polygon_self_intersects(tri) is None


def test_collinear_polygon_is_degenerate():
    # 三点共线时，隐式闭合边与前序边重叠，车辆轮廓面积为零
    shape = [pt(0, 0), pt(500, 0), pt(1000, 0)]
    assert polygon_is_degenerate(shape)


def test_non_degenerate_triangle_has_area():
    assert not polygon_is_degenerate([pt(0, 0), pt(1000, 0), pt(0, 1000)])


def test_bowtie_self_intersecting():
    bow = [pt(0, 0), pt(1000, 1000), pt(1000, 0), pt(0, 1000)]
    pair = polygon_self_intersects(bow)
    assert pair is not None
    # 自交发生在边0 (0,0)->(1000,1000) 与边2 (1000,0)->(0,1000)
    assert pair == (0, 2)


def test_concave_polygon_not_self_intersecting():
    # 凹四边形（箭头形），非自交
    arrow = [pt(0, 0), pt(100, 50), pt(0, 100), pt(30, 50)]
    assert polygon_self_intersects(arrow) is None


def test_nonadjacent_touch_is_self_intersection():
    # 非相邻边共线接触（端点碰对方内部）应判自交
    shape = [pt(0, 0), pt(100, 0), pt(50, 50), pt(50, 0), pt(0, 50)]
    assert polygon_self_intersects(shape) is not None


def test_adjacent_edges_are_allowed():
    # 所有非退化多边形相邻边都共享端点，不得误报
    sq = [pt(0, 0), pt(10, 0), pt(10, 10), pt(0, 10)]
    assert polygon_self_intersects(sq) is None


# ---------- 全局最小距离与并列选择 ----------

def test_minimum_pair_basic_rectangle():
    # 门形隧道（不闭合），矩形车辆，四周 200
    tunnel = [pt(0, 0), pt(0, 1200), pt(1000, 1200), pt(1000, 0)]
    vehicle = [pt(200, 200), pt(800, 200), pt(800, 1000), pt(200, 1000)]
    d, ti, vi = minimum_segment_pair(tunnel, vehicle)
    assert d == pytest.approx(200.0, abs=1e-9)
    # 并列时隧道索引取最小 0；边0 与隧道边0 的最近点就在 (200,200)，距离 200，
    # 且 (0,0) 是遍历顺序中的第一对，故胜出
    assert (ti, vi) == (0, 0)


def test_minimum_pair_intersection_zero():
    tunnel = [pt(0, 0), pt(1000, 0)]
    vehicle = [pt(-100, -100), pt(100, -100), pt(100, 100), pt(-100, 100)]
    d, ti, vi = minimum_segment_pair(tunnel, vehicle)
    assert d == 0.0
    # 隧道边0 与限界边1 (100,-100)->(100,100) 首先相交
    assert (ti, vi) == (0, 1)


def test_minimum_pair_tie_prefers_small_indices():
    # 水平线段隧道，两条完全相同的线段只放一条；
    # 构造两个等距多边形边，验证较小限界边索引胜出
    tunnel = [pt(0, 0), pt(1000, 0)]
    vehicle = [
        pt(100, 100),  # 边0: ->(900,100) 距离100
        pt(900, 100),
        pt(900, 300),  # 边1 竖直，距100
        pt(100, 300),  # 边2 水平，距300
        # 边3 闭合 (100,300)->(100,100) 竖直，距100
    ]
    d, ti, vi = minimum_segment_pair(tunnel, vehicle)
    assert d == pytest.approx(100.0, abs=1e-9)
    assert (ti, vi) == (0, 0)


def test_tie_epsilon_boundary():
    # 直接验证阈值语义：差 <= 1e-9 视为并列保留前者
    assert DISTANCE_TIE_EPS == 1e-9
    # 同一组输入浮点计算完全一致，构造近并列：点到两条对称边
    tunnel = [pt(0, 0), pt(0, 2000)]
    # 车辆矩形右移 100，左右两竖边到隧道距离分别为 100 与 700；
    # 改以两个几乎等距的形状：上下横边端点到竖直线距离都是 x 偏移
    vehicle = [pt(100, 100), pt(200, 100), pt(200, 1900), pt(100, 1900)]
    d, ti, vi = minimum_segment_pair(tunnel, vehicle)
    assert d == pytest.approx(100.0, abs=1e-9)
    # 边0 的端点 (100,100) 距隧道 100；边3 闭合边同样 100 -> 取边0
    assert (ti, vi) == (0, 0)


def test_minimum_pair_closed_edge_can_win():
    # 闭合边（多边形最后一条）可以是最危险边
    tunnel = [pt(0, 400), pt(0, 600)]
    vehicle = [pt(200, 200), pt(400, 400), pt(400, 600), pt(200, 800)]
    d, ti, vi = minimum_segment_pair(tunnel, vehicle)
    assert d == pytest.approx(200.0, abs=1e-9)
    assert vi == 3  # 闭合边
    assert ti == 0


# ---------- 四舍五入 ----------

@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, 0.0),
        (200.0, 200.0),
        (7.071067811865476, 7.071),
        # ROUND_HALF_UP：.0005 进位（银行家舍入会保持 .000）
        (1.0005, 1.001),
        (1.0015, 1.002),
        (2.0004999, 2.0),
        (2.0005001, 2.001),
        (-1.0005, -1.001),
    ],
)
def test_round_three(value, expected):
    assert round_three(value) == expected


def test_minimum_pair_tie_epsilon_float_synthetic():
    # 两条水平隧道边；多边形两个底点 P0/P1 分别正对边0/边1，垂足均在线段内部，
    # 因而两条边的最小距离可由隧道边的 y 坐标精确控制（测试用浮点坐标，
    # 真实接口坐标为毫米整数，构造不出这么小的距离差）。
    polygon = [
        (0.0, 100.0),    # P0 正对边0
        (25.0, 100.0),   # P1 正对边1
        (25.0, 300.0),
        (0.0, 300.0),
    ]

    # 完全相等：较小隧道索引 0 胜出
    tunnel_eq = [
        (0.0, 0.0), (10.0, 0.0),
        (20.0, 0.0), (30.0, 0.0),
    ]
    d, ti, _ = minimum_segment_pair(tunnel_eq, polygon)
    assert d == pytest.approx(100.0, abs=1e-12)
    assert ti == 0

    # 差恰好为 1e-9（<=1e-9）视为并列，仍保留边0
    tunnel_boundary = [
        (0.0, 0.0), (10.0, 0.0),
        (20.0, 1e-9), (30.0, 1e-9),
    ]
    d2, ti2, _ = minimum_segment_pair(tunnel_boundary, polygon)
    assert d2 == pytest.approx(100.0, abs=1e-9)
    assert ti2 == 0

    # 差超过 1e-9：更近的边1 胜出
    tunnel_break = [
        (0.0, 0.0), (10.0, 0.0),
        (20.0, 2e-9), (30.0, 2e-9),
    ]
    d3, ti3, _ = minimum_segment_pair(tunnel_break, polygon)
    assert ti3 == 1
    assert d3 == pytest.approx(100.0 - 2e-9, abs=1e-12)


def test_double_precision_large_coordinates():
    # 坐标在 1e6 量级，距离平方 4e12 仍在双精度精确整数范围内
    tunnel = [pt(-1_000_000, -1_000_000), pt(1_000_000, -1_000_000)]
    vehicle = [
        pt(-1_000_000, -999_000),
        pt(1_000_000, -999_000),
        pt(1_000_000, -999_500),
        pt(-1_000_000, -999_500),
    ]
    d, _, _ = minimum_segment_pair(tunnel, vehicle)
    assert d == pytest.approx(500.0, abs=1e-9)
