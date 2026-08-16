"""A3 分块再校验：recognize_tiled 合并 FigureData 后跑跨块几何/一致性校验；
describe_tiled 超大图分块描述。

关键点：单块内数据不足（如只有 2 个角）→ 块内校验跳过、直出；合并后凑齐
（3 个角 / 跨块向量）→ 重新跑几何校验，跨块矛盾也能被抓出。
"""

from vision_kit import engine
from vision_kit.figure import FigureData

# 块 1：只有 ∠A、∠B（2 个角，块内不触发三角内角和）
BLOCK_ANGLES_AB = (
    '{"figures":{"points":{"A":[0,0],"B":[6,0]},'
    '"angles":[{"vertex":"A","sides":["AB","A?"],"value":60},'
    '{"vertex":"B","sides":["BA","B?"],"value":70}]}}'
)
# 块 2：只有 ∠C（1 个角）
BLOCK_ANGLES_C = (
    '{"figures":{"points":{"C":[2,4]},'
    '"angles":[{"vertex":"C","sides":["C?","C?"],"value":60}]}}'
)
# 块 1：向量 a1 前 2 分量；块 2：a1 后 2 分量 + b
BLOCK_VEC_PART1 = '{"a1":[1,2]}'
BLOCK_VEC_PART2 = '{"a1":[3,4],"b":[3,4]}'
# 完全有效的一块（应通过）
BLOCK_OK = '{"a1":[1,1],"a2":[1,2],"b":[2,3]}'


def _caller_seq(responses):
    it = iter(responses)

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        try:
            return next(it)
        except StopIteration:
            return responses[-1]

    return caller


# ---------------------------------------------------------------------------
# FigureData.merge
# ---------------------------------------------------------------------------


def test_merge_takes_longest_vector_version():
    """同名向量跨块：分量数相同取第一个版本；分量更多者覆盖（边缘块截断分量少）。"""
    p1 = FigureData(BLOCK_VEC_PART1)
    p2 = FigureData(BLOCK_VEC_PART2)
    merged = FigureData.merge([p1, p2])
    assert merged.vectors["a1"] == [1.0, 2.0]  # 两块都是 2 分量 → 取第一个
    assert merged.vectors["b"] == [3.0, 4.0]
    assert merged.has_content
    # 分量更多者覆盖：块 2 的 a1 若完整（4 分量）则覆盖块 1 的截断版
    full = FigureData('{"a1":[1,2,3,4]}')
    merged2 = FigureData.merge([p1, full])
    assert merged2.vectors["a1"] == [1.0, 2.0, 3.0, 4.0]


def test_merge_dedups_duplicate_objects():
    """重叠区同一对象在相邻两块重复出现 → 去重只留一份。"""
    seg = {"endpoints": ["A", "B"], "length": 4}
    raw1 = (
        '{"figures":{"points":{"A":[0,0],"B":[4,0]},"segments":['
        '{"endpoints":["A","B"],"length":4}]}}'
    )
    raw2 = (
        '{"figures":{"points":{"A":[0,0],"B":[4,0]},"segments":['
        '{"endpoints":["A","B"],"length":4}]}}'
    )
    merged = FigureData.merge([FigureData(raw1), FigureData(raw2)])
    assert len(merged.segments) == 1
    assert merged.segments[0] == seg


def test_merge_reruns_geometry_checks():
    """块 1 + 块 2 合并后凑齐 3 个角 → 跨块三角内角和校验触发（60+70+60=190 ≠ 180）。"""
    merged = FigureData.merge([FigureData(BLOCK_ANGLES_AB), FigureData(BLOCK_ANGLES_C)])
    bad = [c for c in merged.geo_checks if not c["passed"]]
    assert any(c["rule"] == "triangle_sum" for c in bad), merged.geo_checks


# ---------------------------------------------------------------------------
# recognize_tiled
# ---------------------------------------------------------------------------


def test_recognize_tiled_returns_merged_figure_data(big_image):
    """分块识别返回合并后的 FigureData（而非文本），并带跨块校验结果。"""
    info = engine.recognize_tiled(big_image, "prompt", _caller_seq([BLOCK_ANGLES_AB, BLOCK_ANGLES_C] * 2))
    assert info is not None
    assert info.has_content
    assert len(info.angles) == 3
    # 跨块三角内角和 60+70+60=190 → 校验失败被保留（不静默丢弃）
    assert any(not c["passed"] for c in info.geo_checks)


def test_recognize_tiled_all_failed_returns_none(big_image):
    """所有区域都失败 → 返回 None。"""
    info = engine.recognize_tiled(big_image, "prompt", _caller_seq(["garbage"] * 8))
    assert info is None


# ---------------------------------------------------------------------------
# describe_tiled
# ---------------------------------------------------------------------------


def test_describe_tiled_concatenates_regions(big_image):
    texts = iter(["左上区域描述", "右上区域描述", "", "右下区域描述"])

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        return next(texts, "")

    out = engine.describe_tiled(big_image, "prompt", caller)
    assert "【区域 1】" in out and "左上区域描述" in out
    assert "【区域 2】" in out
    # 空区域被跳过
    assert "【区域 3】" not in out
    assert "【区域 4】" in out and "右下区域描述" in out


def test_describe_tiled_empty_when_all_empty(big_image):
    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        return ""

    assert engine.describe_tiled(big_image, "prompt", caller) == ""
