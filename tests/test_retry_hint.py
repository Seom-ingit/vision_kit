"""A1 定向重试：engine.retry_hint_for 与 recognize 注入的单元测试。

校验层（geo_checks / consistent）抓到的失败原因必须回流成「定向」重试提示，
而不是笼统的「重新数一遍」：这样第 2/3 次采样重试才带修复指令。
"""

from vision_kit import engine
from vision_kit.figure import FigureData

# 维度不一致：a1 4 维、b 3 维
VALS_DIM = '{"a1":[1,1,2,2],"b":[1,0,2]}'
# 几何不自洽：三角内角和 = 60+60+70 = 190 ≠ 180（points 必须齐全，否则先触发引用校验）
VALS_TRIANGLE_SUM_BAD = (
    '{"figures":{"points":{"A":[0,0],"B":[6,0],"C":[2,4]},'
    '"angles":[{"vertex":"A","sides":["AB","AC"],"value":60},'
    '{"vertex":"B","sides":["BA","BC"],"value":60},'
    '{"vertex":"C","sides":["CA","CB"],"value":70}]}}'
)
# 几何不自洽：向量加法 a1+a2 应 = b，读成近失
VALS_VECTOR_SUM_BAD = '{"a1":[1,1],"a2":[1,2],"b":[2,2]}'
# 维度一致 + 几何自洽
VALS_OK = '{"a1":[1,1],"a2":[1,2],"b":[2,3]}'


def _hint_for(raw: str) -> str:
    return engine.retry_hint_for(FigureData(raw))


# ---------------------------------------------------------------------------
# retry_hint_for 映射
# ---------------------------------------------------------------------------


def test_dimension_mismatch_hint():
    hint = _hint_for(VALS_DIM)
    assert hint == engine._DIMENSION_HINT
    assert "维度" in hint


def test_triangle_sum_rule_hint():
    hint = _hint_for(VALS_TRIANGLE_SUM_BAD)
    assert "180" in hint and "角" in hint


def test_vector_sum_rule_hint():
    hint = _hint_for(VALS_VECTOR_SUM_BAD)
    assert "加法关系" in hint and "a1 + a2 = b" in hint


def test_all_rules_have_dedicated_hints():
    """每条可能判负的规则都有定向提示（除矩阵乘法维度：它只输出正面确认）。"""
    from vision_kit.geometry import check_pythagoras, check_triangle_inequality

    for rule in engine._RULE_HINTS:
        assert rule in (
            "triangle_sum",
            "vector_sum",
            "segment_length",
            "triangle_inequality",
            "pythagoras",
            "plausible_range",
            "circle_radius",
            "diameter_right_angle",
            "similar_proportion",
            "parallelogram",
            "function_symmetry",
        ), rule
    # 占位：确保 geometry 仍导出可判负规则（防止规则改名后映射表失联）
    assert check_triangle_inequality and check_pythagoras


def test_ok_result_no_hint():
    assert engine.retry_hint_for(FigureData(VALS_OK)) == ""


def test_none_no_hint():
    assert engine.retry_hint_for(None) == ""


def test_hint_caps_at_two_rules():
    """多条失败规则时只取前 2 条，避免提示过长。"""
    raw = (
        '{"figures":{"points":{"A":[0,0],"B":[6,0],"C":[2,4]},'
        '"angles":[{"vertex":"A","sides":["AB","AC"],"value":60},'
        '{"vertex":"B","sides":["BA","BC"],"value":60},'
        '{"vertex":"C","sides":["CA","CB"],"value":70}]},'
        '"a1":[1,1],"a2":[1,2],"b":[2,2]}'
    )
    info = FigureData(raw)
    hint = engine.retry_hint_for(info)
    assert hint.count("\n注意：") == 1  # 多条规则合并成一条注意
    assert "180" in hint and "加法关系" in hint  # 两条失败规则都带上了
    assert hint.count("请重新") >= 1


# ---------------------------------------------------------------------------
# recognize 注入行为
# ---------------------------------------------------------------------------


def _caller_seq(responses):
    it = iter(responses)

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        try:
            return next(it)
        except StopIteration:
            return responses[-1]

    return caller


def test_recognize_injects_directed_hint_on_retry():
    """第一次几何不自洽（三角内角和）→ 第二次提示词必须含定向修复指令。"""
    prompts = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        prompts.append(prompt)
        if len(prompts) == 1:
            return VALS_TRIANGLE_SUM_BAD
        return VALS_OK

    info = engine.recognize("x.png", "基础提示词", caller)
    assert info is not None and info.consistent
    assert len(prompts) == 2
    # 第一次：基础提示词（无追加）；第二次：带定向 hint
    assert prompts[0] == "基础提示词"
    assert prompts[1].startswith("基础提示词")
    assert "180" in prompts[1]


def test_recognize_injects_dimension_hint_on_retry():
    """第一次维度不一致 → 第二次提示词含「维度」定向指令。"""
    prompts = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        prompts.append(prompt)
        if len(prompts) == 1:
            return VALS_DIM
        return VALS_OK

    info = engine.recognize("x.png", "p", caller)
    assert info is not None and info.consistent
    assert len(prompts) == 2
    assert "维度" in prompts[1]


def test_recognize_first_attempt_no_hint():
    """首次调用不追加任何重试提示（与旧行为一致）。"""
    prompts = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        prompts.append(prompt)
        return VALS_OK

    engine.recognize("x.png", "p", caller)
    assert prompts == ["p"]
