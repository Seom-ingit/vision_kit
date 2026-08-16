"""figure.py 的 1.1 图元 DOM 单测 —— JSON 优先解析 / 引用完整性 / 向后兼容 / 渲染。"""

from vision_kit.figure import FigureData

# ---------------------------------------------------------------------------
# 图元 JSON 主解析
# ---------------------------------------------------------------------------


def test_parses_full_dom():
    f = FigureData(
        '{"type": "几何图", "figures": {'
        '"points": {"A": [0, 0], "B": [6, 0], "C": [2, 4]},'
        '"segments": [{"endpoints": ["A", "B"], "length": 6}],'
        '"angles": [{"vertex": "A", "sides": ["AB", "AC"], "value": 63.4}],'
        '"circles": [{"center": "A", "radius": 2.5}]'
        '}, "note": "三角形"}'
    )
    assert f.type == "几何图"
    assert f.points == {"A": [0.0, 0.0], "B": [6.0, 0.0], "C": [2.0, 4.0]}
    assert f.segments == [{"endpoints": ["A", "B"], "length": 6.0}]
    assert f.angles == [{"vertex": "A", "sides": ["AB", "AC"], "value": 63.4}]
    assert f.circles == [{"center": "A", "radius": 2.5}]
    assert f.has_content
    assert f.consistent


def test_points_pos_wrapper_form():
    # {"A": {"pos": [0, 0]}} 形态
    f = FigureData('{"figures": {"points": {"A": {"pos": [1, 2]}}}}')
    assert f.points == {"A": [1.0, 2.0]}
    assert f.has_content


def test_dom_reference_integrity_segment():
    # 线段端点引用了未定义的点 → dom_consistent False → consistent False
    f = FigureData(
        '{"figures": {"points": {"A": [0, 0]},'
        '"segments": [{"endpoints": ["A", "X"], "length": 4}]}}'
    )
    assert not f.dom_consistent
    assert not f.consistent
    assert f.has_content


def test_dom_reference_integrity_angle_vertex():
    f = FigureData(
        '{"figures": {"points": {"A": [0, 0]},'
        '"angles": [{"vertex": "Z", "sides": ["ZA", "AB"], "value": 45}]}}'
    )
    assert not f.dom_consistent


def test_dom_reference_integrity_circle_center():
    f = FigureData(
        '{"figures": {"points": {"A": [0, 0]},"circles": [{"center": "O", "radius": 3}]}}'
    )
    assert not f.dom_consistent


def test_empty_figures_is_consistent():
    # 纯向量/矩阵图（无图元）→ 引用校验视为通过（向后兼容）
    f = FigureData('{"a1": [1, 2, 3, 4]}')
    assert f.dom_consistent
    assert f.consistent


def test_points_not_leaked_into_vectors():
    # JSON 主解析成功时，figures 里的点坐标不得被误解析成向量
    f = FigureData('{"figures": {"points": {"A": [0, 0], "B": [1, 2]}}}')
    assert f.vectors == {}
    assert f.points == {"A": [0.0, 0.0], "B": [1.0, 2.0]}


# ---------------------------------------------------------------------------
# 向后兼容：旧格式（顶层裸向量 / quantities 包装 / 正则兜底）
# ---------------------------------------------------------------------------


def test_backward_bare_vectors():
    f = FigureData('{"a1": [1, 1, 2, 2], "a2": [1, 2, 1, 3]}')
    assert f.vectors == {"a1": [1.0, 1.0, 2.0, 2.0], "a2": [1.0, 2.0, 1.0, 3.0]}
    assert f.has_content
    assert f.consistent


def test_backward_quantities_wrapper():
    f = FigureData('{"type": "向量图", "quantities": {"a1": [1, 2, 3]}, "labels": {"a1": "已知"}}')
    assert f.vectors == {"a1": [1.0, 2.0, 3.0]}
    assert f.type == "向量图"


def test_backward_regex_fallback_on_broken_json():
    # JSON 无法解析 → 正则兜底仍能捞出向量
    f = FigureData('prefix junk {"a1": [1, 2, 3], "a2": [4, 5, 6], broken')
    assert f.vectors == {"a1": [1.0, 2.0, 3.0], "a2": [4.0, 5.0, 6.0]}
    assert f.has_content


def test_backward_matrix_priority():
    f = FigureData('{"A": [[1, 2], [3, 4]]}')
    assert "A" in f.matrices
    assert "A" not in f.vectors


# ---------------------------------------------------------------------------
# to_dict / render
# ---------------------------------------------------------------------------


def test_to_dict_includes_figures_and_flags():
    f = FigureData(
        '{"figures": {"points": {"A": [0, 0]},"circles": [{"center": "A", "radius": 3}]}}'
    )
    d = f.to_dict()
    assert d["figures"]["points"] == {"A": [0, 0]}
    assert d["figures"]["circles"] == [{"center": "A", "radius": 3}]
    assert d["consistent"] is True
    assert d["dom_consistent"] is True


def test_render_includes_dom():
    f = FigureData(
        '{"figures": {"points": {"A": [0, 0], "B": [3, 0]},'
        '"segments": [{"endpoints": ["A", "B"], "length": 3}],'
        '"angles": [{"vertex": "A", "sides": ["AB"], "value": 90}]}}'
    )
    text = f.render()
    assert "点A(0,0)" in text
    assert "线段AB 长=3" in text
    assert "角A(AB)=90°" in text
