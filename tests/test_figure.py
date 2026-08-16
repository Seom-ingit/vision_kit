"""figure.py（FigureData）的单元测试 —— 正则解析 / 维度一致 / 结构化输出 / 渲染。

这是整条识别链路里唯一解析模型的模块，纯逻辑、无配置依赖，最适合做高强度单测。
"""

from vision_kit.figure import FigureData

# ---------------------------------------------------------------------------
# 类型 / note 提取
# ---------------------------------------------------------------------------


def test_type_and_note_extracted():
    f = FigureData('{"type": "向量图", "note": "求夹角的题目", "quantities": {}}')
    assert f.type == "向量图"
    assert f.note == "求夹角的题目"


def test_type_note_missing_when_absent():
    f = FigureData("just some text without json")
    assert f.type == ""
    assert f.note == ""


# ---------------------------------------------------------------------------
# 向量解析
# ---------------------------------------------------------------------------


def test_parses_vectors():
    f = FigureData('{"a1": [1, 1, 2, 2], "a2": [1, 2, 1, 3], "b": [1, 0, 2, 3]}')
    assert f.vectors == {"a1": [1.0, 1.0, 2.0, 2.0], "a2": [1.0, 2.0, 1.0, 3.0],
                         "b": [1.0, 0.0, 2.0, 3.0]}
    assert f.has_content
    assert f.consistent


def test_parses_negative_and_scientific_notation():
    # 支持 + 号、负数、科学计数法（_NUM_CLS 字符类覆盖）
    f = FigureData('{"p": [1e-3, -2, +3.5]}')
    assert f.vectors["p"] == [0.001, -2.0, 3.5]


def test_ignores_non_numeric_vector():
    # 含非数字（如字母占位）→ 整组丢弃，不得污染
    f = FigureData('{"bad": [1, 2, "x", 4]}')
    assert f.vectors == {}
    assert not f.has_content


def test_matrix_vs_vector_same_name():
    # 同名时矩阵优先，向量不重复收录
    f = FigureData('{"A": [[1, 2], [3, 4]]}')
    assert "A" in f.matrices
    assert "A" not in f.vectors


# ---------------------------------------------------------------------------
# 矩阵解析
# ---------------------------------------------------------------------------


def test_parses_matrix():
    f = FigureData('{"M": [[1, 2, 3], [4, 5, 6]]}')
    assert f.matrices == {"M": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]}
    assert f.has_content
    assert f.consistent


def test_ragged_matrix_ignored():
    # 行列不齐 → 该矩阵丢弃
    f = FigureData('{"M": [[1, 2], [3, 4, 5]]}')
    assert f.matrices == {}


def test_matrix_row_with_non_numeric_dropped():
    f = FigureData('{"M": [[1, 2], [3, "x"]]}')
    assert f.matrices == {}


# ---------------------------------------------------------------------------
# 维度一致性
# ---------------------------------------------------------------------------


def test_inconsistent_vector_dimension_flagged():
    # 最典型的漏数：a1 4 维、b 3 维
    f = FigureData('{"a1": [1, 1, 2, 2], "b": [1, 0, 2]}')
    assert not f.consistent
    # has_content 与 dimension 无关，仍应为真（有内容）
    assert f.has_content


def test_single_vector_consistent():
    f = FigureData('{"v": [1, 2, 3]}')
    assert f.consistent


def test_empty_items_consistent():
    f = FigureData("")
    assert f.consistent
    assert not f.has_content


def test_inconsistent_2x2_matrix_shape():
    f = FigureData('{"A": [[1, 2], [3, 4]], "B": [[1, 2, 3]]}')
    assert not f.consistent


# ---------------------------------------------------------------------------
# geo_checks 联动（figure → geometry）
# ---------------------------------------------------------------------------


def test_geo_checks_attached():
    # a1+a2 应 = b，但 b 差一点 → 几何不自洽（vector_sum near-miss）
    f = FigureData('{"a1": [1, 1], "a2": [1, 2], "b": [2, 2]}')
    bad = [c for c in f.geo_checks if c["rule"] == "vector_sum" and not c["passed"]]
    assert bad


# ---------------------------------------------------------------------------
# to_dict / render
# ---------------------------------------------------------------------------


def test_to_dict_int_coercion():
    f = FigureData('{"a1": [1.0, 2.5], "type": "向量图"}')
    d = f.to_dict()
    assert d["vectors"]["a1"] == [1, 2.5]  # 整数值变 int，小数保留 float
    assert d["type"] == "向量图"
    assert "geo_checks" in d
    assert "text" in d
    assert "raw" in d


def test_render_includes_vectors_and_note():
    f = FigureData('{"a1": [1, 2], "note": "一些说明"}')
    text = f.render()
    assert "a1=[1, 2]" in text
    assert "一些说明" in text
    assert "图形类型" not in text  # 无 type 时不含
