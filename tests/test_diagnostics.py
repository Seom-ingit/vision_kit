"""2.3 诊断（error_code / 置信度）、1.5 TikZ 输出、H1 防幻觉提示词的单测。"""

from vision_kit import engine
from vision_kit.figure import FigureData

# ---------------------------------------------------------------------------
# H1：extract 空结果防幻觉（提示词硬性要求 + 空结果合法返回）
# ---------------------------------------------------------------------------


def test_structured_prompt_forbids_fabrication():
    prompt = engine.STRUCTURED_PROMPT
    assert "严禁编造" in prompt
    assert "没有任何带数字的标注" in prompt
    assert "vectors 与 matrices 必须输出 {}" in prompt


def test_empty_vectors_is_legal_result():
    # 模型明确输出"无数字标注" → 合法空结果，不是幻觉填充
    f = FigureData('{"type": "电路图", "vectors": {}, "matrices": {}, "note": "图中无数字标注"}')
    assert not f.has_content
    assert f.error_code == "NO_NUMERIC_CONTENT"
    assert f.confidence == "low"


# ---------------------------------------------------------------------------
# D1：错误码
# ---------------------------------------------------------------------------


def test_error_code_ok():
    f = FigureData('{"a1": [1, 1], "a2": [1, 2], "b": [2, 3]}')
    assert f.error_code == "OK"
    assert f.confidence == "high"


def test_error_code_dimension_mismatch():
    f = FigureData('{"a1": [1, 1, 2, 2], "b": [1, 0, 2]}')
    assert f.error_code == "DIMENSION_MISMATCH"
    assert f.confidence == "low"


def test_error_code_geometry_inconsistent():
    f = FigureData('{"a1": [1, 1], "a2": [1, 2], "b": [2, 2]}')
    assert f.error_code == "GEOMETRY_INCONSISTENT"
    assert f.confidence == "low"


def test_error_code_parse_failed():
    f = FigureData("totally ;; not json")
    assert f.error_code == "PARSE_FAILED"
    assert f.confidence == "low"


# ---------------------------------------------------------------------------
# D1：attempts / diagnostics / item_confidence
# ---------------------------------------------------------------------------


def test_attempts_wired_by_engine():
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        return '{"a1": [1, 1], "a2": [1, 2], "b": [2, 2]}'  # 几何不自洽 → 触发重试

    info = engine.recognize("x.png", "prompt", caller)
    assert info is not None
    assert info.attempts == engine.MAX_ATTEMPTS  # 重试耗尽
    d = info.to_dict()
    assert d["diagnostics"]["retried"] is True
    assert d["diagnostics"]["attempts"] == engine.MAX_ATTEMPTS
    assert d["diagnostics"]["error_code"] == "GEOMETRY_INCONSISTENT"


def test_attempts_single_call():
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        return '{"a1": [1, 1], "a2": [1, 2], "b": [2, 3]}'

    info = engine.recognize("x.png", "prompt", caller)
    assert info is not None
    assert info.attempts == 1
    assert info.to_dict()["diagnostics"]["confidence"] == "high"


def test_item_confidence_downgrades_failed_names():
    f = FigureData('{"a1": [1, 1], "a2": [1, 2], "b": [2, 2]}')
    conf = f.item_confidence
    assert conf["a1"] == "low" and conf["a2"] == "low" and conf["b"] == "low"


def test_item_confidence_high_when_ok():
    f = FigureData('{"a1": [1, 1], "a2": [1, 2], "b": [2, 3]}')
    assert f.item_confidence == {"a1": "high", "a2": "high", "b": "high"}


# ---------------------------------------------------------------------------
# D2：TikZ 输出
# ---------------------------------------------------------------------------


def test_tikz_geometry_dom():
    f = FigureData(
        '{"type": "几何图", "figures": {'
        '"points": {"A": [0, 0], "B": [6, 0], "C": [2, 4]},'
        '"segments": [{"endpoints": ["A", "B"], "length": 6}],'
        '"angles": [{"vertex": "A", "sides": ["AB", "AC"], "value": 63.4}],'
        '"circles": [{"center": "A", "radius": 2.5}]'
        "}}"
    )
    t = f.to_tikz()
    assert t is not None
    assert "\\begin{tikzpicture}" in t and "\\end{tikzpicture}" in t
    assert "\\coordinate (A) at (0, 0);" in t
    assert "\\draw (A) -- (B) node[midway,above] {6};" in t
    assert "\\draw (A) circle (2.5);" in t
    assert "63.4" in t


def test_tikz_vectors():
    f = FigureData('{"a1": [1, 2], "b": [0, 0, 3, 4]}')
    t = f.to_tikz()
    assert t is not None
    assert "\\draw[->] (0,0) -- (1, 2) node[midway,above] {a1};" in t
    assert "\\draw[->] (0, 0) -- (3, 4) node[midway,above] {b};" in t


def test_tikz_none_for_matrix_only():
    f = FigureData('{"A": [[1, 2], [3, 4]]}')
    assert f.to_tikz() is None
