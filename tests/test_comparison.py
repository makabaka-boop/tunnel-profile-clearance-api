"""断面比对服务（纯 Python 层）契约测试。"""

from app.comparison import align_to_reference, compare_profiles


def test_align_to_reference_returns_correction_and_corrected_sequence():
    # 本期整体平移 (-10, -5)：修正量 = 基准基准点 - 本期基准点 = (+10, +5)，
    # 修正后序列保持本期输入顺序
    dx, dy, corrected = align_to_reference(
        [("A", 100, 200), ("B", 300, 400)],
        [("A", 90, 195), ("B", 290, 395)],
        "A",
    )
    assert (dx, dy) == (10, 5)
    assert corrected == [("A", 100, 200), ("B", 300, 400)]

    # 基准点不必是首点：以 B 为基准点时修正量由 B 决定
    dx2, dy2, corrected2 = align_to_reference(
        [("A", 100, 200), ("B", 300, 400)],
        [("A", 90, 195), ("B", 290, 395)],
        "B",
    )
    assert (dx2, dy2) == (10, 5)
    assert corrected2 == corrected


def test_correction_is_reference_difference():
    # 本期整体平移 (-10, -5)：修正量 = 基准基准点 - 本期基准点 = (+10, +5)
    result = compare_profiles(
        [("A", 100, 200), ("B", 300, 400)],
        [("A", 90, 195), ("B", 290, 395)],
        "A",
        5,
    )
    assert (result.dx, result.dy) == (10, 5)
    assert [(p.x, p.y) for p in result.points] == [(100, 200), (300, 400)]
    assert [p.displacement_mm for p in result.points] == [0.0, 0.0]
    assert result.exceeded_names == []
    assert result.all_passed is True
    # 全部并列 0，最大位移取输入顺序最前者
    assert result.max_displacement_name == "A"
    assert result.max_displacement_mm == 0.0


def test_unrounded_distance_decides_tolerance():
    # sqrt(2) ≈ 1.41421：容差 1 超限，容差 2 合格；输出保留三位小数
    baseline = [("A", 0, 0), ("B", 0, 0)]
    current = [("A", 0, 0), ("B", 1, 1)]

    over = compare_profiles(baseline, current, "A", 1)
    assert over.points[1].displacement_mm == 1.414
    assert over.exceeded_names == ["B"]
    assert over.all_passed is False

    within = compare_profiles(baseline, current, "A", 2)
    assert within.exceeded_names == []
    assert within.all_passed is True


def test_tolerance_boundary_equal_passes():
    # 位移恰好等于容差（3-4-5 直角边）判合格
    result = compare_profiles(
        [("A", 0, 0), ("B", 0, 0)],
        [("A", 0, 0), ("B", 3, 4)],
        "A",
        5,
    )
    assert result.points[1].displacement_mm == 5.0
    assert result.exceeded_names == []
    assert result.all_passed is True


def test_max_tie_keeps_earlier_point():
    # B 位移 hypot(6,8)=10，C 位移 hypot(-8,6)=10，并列取输入顺序靠前的 B
    result = compare_profiles(
        [("A", 0, 0), ("B", 100, 0), ("C", 200, 0)],
        [("A", 0, 0), ("B", 106, 8), ("C", 192, 6)],
        "A",
        20,
    )
    assert result.max_displacement_name == "B"
    assert result.max_displacement_mm == 10.0

    # 交换输入顺序后，同一对并列位移的最大测点变为 C
    swapped = compare_profiles(
        [("A", 0, 0), ("C", 200, 0), ("B", 100, 0)],
        [("A", 0, 0), ("C", 192, 6), ("B", 106, 8)],
        "A",
        20,
    )
    assert swapped.max_displacement_name == "C"


def test_max_displacement_value_rounded_three():
    result = compare_profiles(
        [("A", 0, 0), ("B", 0, 0)],
        [("A", 0, 0), ("B", 1, 1)],
        "A",
        5,
    )
    assert result.max_displacement_name == "B"
    assert result.max_displacement_mm == 1.414


def test_exceeded_names_follow_input_order():
    result = compare_profiles(
        [("A", 0, 0), ("B", 100, 0), ("C", 200, 0), ("D", 300, 0)],
        [("A", 0, 0), ("B", 100, 9), ("C", 200, 1), ("D", 300, 7)],
        "A",
        5,
    )
    assert result.exceeded_names == ["B", "D"]
    assert result.all_passed is False
    assert result.max_displacement_name == "B"
