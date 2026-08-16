"""stats.py（4.3 统计图 → 数据表）与 prompts.py（4.1 场景化模板）的单测。"""

from vision_kit import engine
from vision_kit.prompts import PROMPT_TEMPLATES
from vision_kit.stats import StatsData, parse_stats, recognize_stats, run_stats_checks

# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def test_parse_stats_full():
    raw = (
        '{"type": "柱状图", "categories": ["一月", "二月", "三月"],'
        '"series": [{"name": "销量", "values": [120, 150, 98]}], "note": "月度销量"}'
    )
    t, note, cats, series = parse_stats(raw)
    assert t == "柱状图"
    assert note == "月度销量"
    assert cats == ["一月", "二月", "三月"]
    assert series == [{"name": "销量", "values": [120.0, 150.0, 98.0]}]


def test_parse_stats_bad_json_empty():
    t, note, cats, series = parse_stats("garbage")
    assert (t, note, cats, series) == ("", "", [], [])


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def test_series_align_fail():
    checks = run_stats_checks("柱状图", ["一", "二", "三"], [{"name": "s", "values": [1, 2]}])
    bad = [c for c in checks if c["rule"] == "series_align" and not c["passed"]]
    assert bad


def test_percent_sum_ok_and_fail():
    ok = run_stats_checks("饼图", ["A", "B"], [{"name": "p", "values": [60, 40]}])
    assert all(c["passed"] for c in ok)
    bad = run_stats_checks("饼图", ["A", "B"], [{"name": "p", "values": [70, 40]}])
    assert [c for c in bad if c["rule"] == "percent_sum" and not c["passed"]]


def test_nonneg_fail():
    checks = run_stats_checks("柱状图", ["A"], [{"name": "s", "values": [-1]}])
    assert [c for c in checks if c["rule"] == "nonneg" and not c["passed"]]


# ---------------------------------------------------------------------------
# StatsData / render / to_dict
# ---------------------------------------------------------------------------


def test_stats_data_consistent_and_dict():
    raw = (
        '{"type": "饼图", "categories": ["甲", "乙"],'
        '"series": [{"name": "占比", "values": [60, 40]}]}'
    )
    d = StatsData(raw)
    assert d.has_content
    assert d.consistent
    out = d.to_dict()
    assert out["categories"] == ["甲", "乙"]
    assert out["series"] == [{"name": "占比", "values": [60, 40]}]
    assert "百分比之和" in out["text"]


# ---------------------------------------------------------------------------
# recognize_stats 重试
# ---------------------------------------------------------------------------


def test_recognize_stats_retries_then_ok():
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        if len(calls) == 1:
            # 系列长度与类别数不一致 → 触发采样重试
            return (
                '{"type": "柱状图", "categories": ["A", "B"],'
                '"series": [{"name": "s", "values": [1]}]}'
            )
        return (
            '{"type": "柱状图", "categories": ["A", "B"],'
            '"series": [{"name": "s", "values": [1, 2]}]}'
        )

    info = recognize_stats("x.png", caller)
    assert info is not None
    assert len(calls) == 2
    assert info.consistent


def test_recognize_stats_returns_best_on_failure():
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        return (
            '{"type": "柱状图", "categories": ["A", "B"],'
            '"series": [{"name": "s", "values": [1]}]}'
        )

    info = recognize_stats("x.png", caller)
    assert info is not None
    assert len(calls) == engine.MAX_ATTEMPTS
    assert not info.consistent


def test_recognize_stats_empty_legal():
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        return '{"type": "柱状图", "categories": [], "series": [], "note": "图中无数据"}'

    info = recognize_stats("x.png", caller)
    assert info is not None
    assert len(calls) == 1  # 合法空结果不重试


# ---------------------------------------------------------------------------
# prompts.py（4.1）
# ---------------------------------------------------------------------------


def test_prompt_templates_exist():
    assert PROMPT_TEMPLATES["default"] == engine.STRUCTURED_PROMPT
    for key in ("geometry", "function", "vector", "statistics"):
        assert key in PROMPT_TEMPLATES
        assert PROMPT_TEMPLATES[key].startswith(engine.STRUCTURED_PROMPT)
        assert len(PROMPT_TEMPLATES[key]) > len(engine.STRUCTURED_PROMPT)
