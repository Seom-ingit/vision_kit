"""A2 几何规则扩充：圆定理 / 相似比例 / 平行四边形 / 函数对称 的单元测试。

覆盖：
- check_circle_radius：圆上点到圆心距离 vs 半径（可判负）；
- check_diameter_right_angle：直径所对圆周角 = 90°（可判负，数学事实）；
- check_similar_proportion：两组三角形三边成比例（仅正面确认）；
- check_parallelogram：quads 声明平行四边形后对边相等（可判负）；
- check_function_symmetry：抛物线对称轴 = 两零点中点（命名约定，可判负）；
- figure.py 解析：circles.on / quads 图元 + dom_consistent 引用校验。
"""

from vision_kit.figure import FigureData
from vision_kit.geometry import overall_consistent, run_geometry_checks


def _checks(data: dict) -> list[dict]:
    return run_geometry_checks(data)


# ---------------------------------------------------------------------------
# circle_radius
# ---------------------------------------------------------------------------


def test_circle_radius_ok():
    data = {
        "points": {"O": [0, 0], "P": [3, 0], "Q": [0, 3]},
        "figures": {"circles": [{"center": "O", "radius": 3.0, "on": ["P", "Q"]}]},
    }
    checks = _checks(data)
    cr = [c for c in checks if c["rule"] == "circle_radius"]
    assert cr and all(c["passed"] for c in cr), checks
    assert overall_consistent(checks)


def test_circle_radius_on_point_wrong():
    # P 距圆心 4 而半径 3（|4-3|=1 > 0.2*4=0.8）→ 判负
    data = {
        "points": {"O": [0, 0], "P": [4, 0], "Q": [0, 3]},
        "figures": {"circles": [{"center": "O", "radius": 3.0, "on": ["P", "Q"]}]},
    }
    checks = _checks(data)
    bad = [c for c in checks if c["rule"] == "circle_radius" and not c["passed"]]
    assert bad, checks
    assert not overall_consistent(checks)


def test_circle_radius_no_on_skips():
    # 无 on 时无法判定"点是否在圆上"，不判负；但输出"未校验"信号
    # （passed=True，error_code 不受影响），让上层知道圆定理校验被跳过
    # （P2-1：circ_adv 曾因模型漏 on 而静默直出 OK）。
    data = {
        "points": {"O": [0, 0], "P": [4, 0]},
        "figures": {"circles": [{"center": "O", "radius": 3.0}]},
    }
    checks = _checks(data)
    sig = [c for c in checks if c["rule"] == "circle_radius"]
    assert sig and all(c["passed"] for c in sig), checks
    assert "未校验" in sig[0]["detail"]
    assert overall_consistent(checks)


# ---------------------------------------------------------------------------
# diameter_right_angle
# ---------------------------------------------------------------------------


def test_diameter_right_angle_ok():
    # 圆 O r=3，A=(-3,0) B=(3,0) 是直径两端，C=(0,3) 在圆上，∠ACB=90°
    data = {
        "points": {"O": [0, 0], "A": [-3, 0], "B": [3, 0], "C": [0, 3]},
        "figures": {
            "circles": [{"center": "O", "radius": 3.0, "on": ["A", "B", "C"]}],
            "angles": [{"vertex": "C", "sides": ["CA", "CB"], "value": 90.0}],
        },
    }
    checks = _checks(data)
    dr = [c for c in checks if c["rule"] == "diameter_right_angle"]
    assert dr and all(c["passed"] for c in dr), checks


def test_diameter_right_angle_contradiction():
    # 圆 O r=3，C=(0,3) 标注直角 90°，但其余圆上点 A=(2,0) B=(-2,0) 距离 4 ≠ 2r=6
    data = {
        "points": {"O": [0, 0], "A": [2, 0], "B": [-2, 0], "C": [0, 3]},
        "figures": {
            "circles": [{"center": "O", "radius": 3.0, "on": ["A", "B", "C"]}],
            "angles": [{"vertex": "C", "sides": ["CA", "CB"], "value": 90.0}],
        },
    }
    checks = _checks(data)
    bad = [c for c in checks if c["rule"] == "diameter_right_angle" and not c["passed"]]
    assert bad, checks
    assert not overall_consistent(checks)


def test_diameter_right_angle_insufficient_skips():
    # on 只有 2 个点（含直角顶点）→ 无法构成"另两点"，跳过
    data = {
        "points": {"O": [0, 0], "B": [3, 0], "C": [0, 3]},
        "figures": {
            "circles": [{"center": "O", "radius": 3.0, "on": ["B", "C"]}],
            "angles": [{"vertex": "C", "sides": ["CB", "C?"], "value": 90.0}],
        },
    }
    dr = [c for c in _checks(data) if c["rule"] == "diameter_right_angle"]
    assert dr == []


# ---------------------------------------------------------------------------
# similar_proportion
# ---------------------------------------------------------------------------


def test_similar_triangles_positive_confirmation():
    # 两组三角形（6 条边、6 个端点、每端点度 2）：3-4-5 与 6-8-10 成比例 2
    segments = [
        {"endpoints": ["A", "B"], "length": 3.0},
        {"endpoints": ["B", "C"], "length": 4.0},
        {"endpoints": ["C", "A"], "length": 5.0},
        {"endpoints": ["D", "E"], "length": 6.0},
        {"endpoints": ["E", "F"], "length": 8.0},
        {"endpoints": ["F", "D"], "length": 10.0},
    ]
    data = {"points": {}, "segments": segments, "figures": {}}
    checks = _checks(data)
    sp = [c for c in checks if c["rule"] == "similar_proportion"]
    assert sp and all(c["passed"] for c in sp), checks


def test_dissimilar_triangles_no_conclusion():
    # 3-4-5 与 3-4-8 明显不成比例（5/8=0.625，超出 20% 容差）→ 无结论（不判负，避免误报）
    segments = [
        {"endpoints": ["A", "B"], "length": 3.0},
        {"endpoints": ["B", "C"], "length": 4.0},
        {"endpoints": ["C", "A"], "length": 5.0},
        {"endpoints": ["D", "E"], "length": 3.0},
        {"endpoints": ["E", "F"], "length": 4.0},
        {"endpoints": ["F", "D"], "length": 8.0},
    ]
    data = {"points": {}, "segments": segments, "figures": {}}
    sp = [c for c in _checks(data) if c["rule"] == "similar_proportion"]
    assert sp == []


def test_similar_proportion_insufficient():
    # 只有 1 个三角形（3 条边）→ similar_proportion 不判定（其余规则可另行输出）
    segments = [
        {"endpoints": ["A", "B"], "length": 3.0},
        {"endpoints": ["B", "C"], "length": 4.0},
        {"endpoints": ["C", "A"], "length": 5.0},
    ]
    data = {"points": {}, "segments": segments, "figures": {}}
    sp = [c for c in _checks(data) if c["rule"] == "similar_proportion"]
    assert sp == []


# ---------------------------------------------------------------------------
# parallelogram
# ---------------------------------------------------------------------------


def test_parallelogram_ok():
    data = {
        "points": {"A": [0, 0], "B": [4, 0], "C": [6, 3], "D": [2, 3]},
        "segments": [
            {"endpoints": ["A", "B"], "length": 4.0},
            {"endpoints": ["B", "C"], "length": 4.0},
            {"endpoints": ["C", "D"], "length": 4.0},
            {"endpoints": ["D", "A"], "length": 4.0},
        ],
        "figures": {"quads": [{"vertices": ["A", "B", "C", "D"], "type": "parallelogram"}]},
    }
    checks = _checks(data)
    pa = [c for c in checks if c["rule"] == "parallelogram"]
    assert pa and all(c["passed"] for c in pa), checks
    assert overall_consistent(checks)


def test_parallelogram_unequal_opposite_sides():
    # 对边 AB=4 但 CD=6 → 判负
    data = {
        "points": {"A": [0, 0], "B": [4, 0], "C": [6, 3], "D": [2, 3]},
        "segments": [
            {"endpoints": ["A", "B"], "length": 4.0},
            {"endpoints": ["B", "C"], "length": 4.0},
            {"endpoints": ["C", "D"], "length": 6.0},
            {"endpoints": ["D", "A"], "length": 4.0},
        ],
        "figures": {"quads": [{"vertices": ["A", "B", "C", "D"], "type": "平行四边形"}]},
    }
    checks = _checks(data)
    bad = [c for c in checks if c["rule"] == "parallelogram" and not c["passed"]]
    assert bad, checks
    assert not overall_consistent(checks)


def test_parallelogram_no_declaration_skips():
    # 无 quads 声明 → 不判定（避免把任意四边形误判为平行四边形）
    data = {
        "points": {"A": [0, 0], "B": [4, 0], "C": [6, 3], "D": [2, 3]},
        "segments": [
            {"endpoints": ["A", "B"], "length": 4.0},
            {"endpoints": ["B", "C"], "length": 4.0},
            {"endpoints": ["C", "D"], "length": 6.0},
            {"endpoints": ["D", "A"], "length": 4.0},
        ],
        "figures": {},
    }
    pa = [c for c in _checks(data) if c["rule"] == "parallelogram"]
    assert pa == []


# ---------------------------------------------------------------------------
# function_symmetry
# ---------------------------------------------------------------------------


def test_function_symmetry_ok():
    # 抛物线零点 z1=-2、z2=2，顶点 vertex=(0,2) → 对称轴 x=0 = 中点
    data = {
        "points": {"z1": [-2, 0], "z2": [2, 0], "vertex": [0, 2]},
        "figures": {},
    }
    checks = _checks(data)
    fs = [c for c in checks if c["rule"] == "function_symmetry"]
    assert fs and all(c["passed"] for c in fs), checks


def test_function_symmetry_mismatch():
    # 顶点 x=1 而两零点中点 x=0 → 判负
    data = {
        "points": {"z1": [-2, 0], "z2": [2, 0], "vertex": [1, 2]},
        "figures": {},
    }
    checks = _checks(data)
    bad = [c for c in checks if c["rule"] == "function_symmetry" and not c["passed"]]
    assert bad, checks
    assert not overall_consistent(checks)


def test_function_symmetry_no_naming_skips():
    # 没有 z1/z2/vertex 命名 → 跳过（不把普通点集误判为抛物线）
    data = {"points": {"A": [-2, 0], "B": [2, 0], "C": [1, 2]}, "figures": {}}
    fs = [c for c in _checks(data) if c["rule"] == "function_symmetry"]
    assert fs == []


# ---------------------------------------------------------------------------
# figure.py 解析：circles.on / quads + dom_consistent
# ---------------------------------------------------------------------------


def test_figure_parses_circles_on_and_quads():
    raw = (
        '{"type":"几何图","figures":{"points":{"O":[0,0],"P":[3,0],"Q":[0,3],'
        '"A":[0,0],"B":[4,0],"C":[6,3],"D":[2,3]},'
        '"circles":[{"center":"O","radius":3,"on":["P","Q"]}],'
        '"quads":[{"vertices":["A","B","C","D"],"type":"parallelogram"}]}}'
    )
    fd = FigureData(raw)
    assert fd.circles[0]["on"] == ["P", "Q"]
    assert fd.quads[0]["type"] == "parallelogram"
    assert fd.quads[0]["vertices"] == ["A", "B", "C", "D"]
    assert fd.dom_consistent
    out = fd.to_dict()
    assert out["figures"]["circles"][0]["on"] == ["P", "Q"]
    assert out["figures"]["quads"][0]["type"] == "parallelogram"


def test_figure_quads_dangling_vertex_inconsistent():
    # quads 引用了未定义的点 X → dom_consistent=False
    raw = (
        '{"figures":{"points":{"A":[0,0],"B":[4,0],"C":[6,3],"D":[2,3]},'
        '"quads":[{"vertices":["A","B","C","X"],"type":"parallelogram"}]}}'
    )
    fd = FigureData(raw)
    assert fd.dom_consistent is False
    assert fd.error_code == "DIMENSION_MISMATCH"  # 引用失败按维度级处理


def test_figure_render_shows_quads_and_on():
    raw = (
        '{"figures":{"points":{"O":[0,0],"P":[3,0],"A":[0,0],"B":[4,0],"C":[6,3],"D":[2,3]},'
        '"circles":[{"center":"O","radius":3,"on":["P"]}],'
        '"quads":[{"vertices":["A","B","C","D"],"type":"平行四边形"}]}}'
    )
    fd = FigureData(raw)
    text = fd.render()
    assert "圆上点:P" in text
    assert "平行四边形ABCD" in text
