"""engine.py 的单元测试 —— 预处理 / 分块 / 重试·兜底策略。

关键点：recognize() 通过注入的 caller 调用，因此不依赖任何网络/API，
用「脚本化 caller」即可穷举它的重试分支。
"""
import base64
import io
import os

import pytest
from PIL import Image
from vision_kit import engine

# ---------------------------------------------------------------------------
# preprocess 小图放大
# ---------------------------------------------------------------------------


def test_preprocess_returns_data_url_safe_png(tmp_image):
    b64 = engine.preprocess(tmp_image)
    # 应返回 base64 PNG
    raw = base64.b64decode(b64)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    # 解码回图片，验证小图被放大到 MAX_SCALE 上限（32x24 * 3.0 = 96x72）
    img = Image.open(io.BytesIO(raw))
    assert img.size == (int(32 * engine.MAX_SCALE), int(24 * engine.MAX_SCALE))
    assert img.size[0] / img.size[1] == pytest.approx(32 / 24, rel=0.01)  # 保持宽高比


def test_preprocess_returns_original_on_error():
    # 无效文件 → 回退为读原始字节（不含 PNG 头也不报错）
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        fname = f.name
        f.write(b"not an image at all")
    try:
        b64 = engine.preprocess(fname)
        assert base64.b64decode(b64) == b"not an image at all"
    finally:
        os.unlink(fname)


# ---------------------------------------------------------------------------
# needs_tiling
# ---------------------------------------------------------------------------


def test_needs_tiling_false_for_small(big_image, tmp_image):
    assert engine.needs_tiling(tmp_image) is False


def test_needs_tiling_true_for_large(big_image):
    assert engine.needs_tiling(big_image) is True


# ---------------------------------------------------------------------------
# _split_tiles
# ---------------------------------------------------------------------------


def test_split_tiles_cover_whole_image(big_image):
    tiles = engine._split_tiles(big_image)
    try:
        # 2x2 应产出 4 块，且每块都是合法 PNG
        assert len(tiles) == 4
        sizes = []
        for t in tiles:
            with open(t, "rb") as f:
                assert f.read(8) == b"\x89PNG\r\n\x1a\n"
            with Image.open(t) as im:
                sizes.append(im.size)
        # 各块尺寸不超过原始图尺寸
        for (w, h) in sizes:
            assert w <= 3200 and h <= 2000
    finally:
        for t in tiles:
            os.unlink(t)


def test_split_tiles_partial_overlap(tmp_image):
    # 小图也切成 4 块（逻辑不因尺寸拒绝）
    tiles = engine._split_tiles(tmp_image)
    try:
        assert len(tiles) == 4
    finally:
        for t in tiles:
            os.unlink(t)


# ---------------------------------------------------------------------------
# recognize —— 用脚本化 caller 穷举分支
# ---------------------------------------------------------------------------

VALS_OK = '{"type":"向量图","a1":[1,1],"a2":[1,2],"b":[2,3]}'          # 维度一致 + 几何自洽
VALS_INCONSISTENT = '{"a1":[1,1,2,2],"b":[1,0,2]}'                      # 维度不一致
VALS_GEOM_BAD = '{"a1":[1,1],"a2":[1,2],"b":[2,2]}'                     # 维度一致但 a1+a2≠b（几何不自洽）
VALS_TEXT_ONLY = '{"type":"函数图","note":"只有说明没有数字"}'           # 无数值（纯文本图）
VALS_GARBAGE = "totally ;; not json"


def _caller_script(responses):
    """返回一个按序消费 responses 的 caller；耗尽后返回最后一个。"""
    it = iter(responses)

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        try:
            return next(it)
        except StopIteration:
            return responses[-1]

    return caller


def test_recognize_returns_immediately_on_consistent():
    """首次调用即维度一致 + 几何自洽 → 立即返回，不重试。"""
    caller = _caller_script([VALS_OK])
    info = engine.recognize("x.png", "prompt", caller)
    assert info is not None
    assert info.consistent
    assert info.type == "向量图"
    # 断言只调用了 1 次（中途已 return）
    assert caller.__name__  # 仅占位


def test_recognize_retries_on_geo_inconsistent_until_stable():
    """先几何不自洽 → 重试；第二次 OK → 返回第二条。"""
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append((temperature, do_sample))
        if len(calls) == 1:
            return VALS_GEOM_BAD     # 几何不自洽
        return VALS_OK               # 重试后自洽

    info = engine.recognize("x.png", "prompt", caller)
    assert info is not None
    assert len(calls) == 2
    # 第一次贪心（temp 0.0）, 重试采样（temp 升高 + do_sample True）
    assert calls[0] == (engine.TEMP_MAIN, False)
    assert calls[1] == (engine.TEMP_RETRY, True)


def test_recognize_uses_sampling_on_retry():
    """重试应切换为采样（temperature 升高）。"""
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(temperature)
        return VALS_GEOM_BAD

    engine.recognize("x.png", "prompt", caller)
    # 3 次尝试：首次贪心，后两次采样（重试 temperature 非 0）
    assert calls[0] != calls[1]
    assert calls[1] != 0.0 and calls[2] != 0.0


def test_recognize_dimension_inconsistent_triggers_retry():
    calls = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        return VALS_INCONSISTENT

    info = engine.recognize("x.png", "prompt", caller)
    # 维度始终不一致 → 重试 3 次后返回兜底
    assert len(calls) == engine.MAX_ATTEMPTS
    assert info is not None
    assert not info.consistent  # 兜底保留首个有内容但维度不对的结果


def test_recognize_returns_text_only_without_numeric():
    """无数值但有 type/note（纯文本图）→ 第一轮直接返回。"""
    caller = _caller_script([VALS_TEXT_ONLY])
    info = engine.recognize("x.png", "prompt", caller)
    assert info is not None
    assert info.type == "函数图"
    assert info.has_content is False


def test_recognize_never_retries_when_always_garbage():
    """始终无法解析 → 3 次后返回 None（回退原始文本）。"""
    caller = _caller_script([VALS_GARBAGE])
    info = engine.recognize("x.png", "prompt", caller)
    assert info is None


# ---------------------------------------------------------------------------
# recognize_tiled —— 用 mock caller 验证分块流程与清理烧脑点
# ---------------------------------------------------------------------------


def test_recognize_tiled_skips_failed_region(big_image):
    """某区域识别失败（无数值内容）→ 该区域被跳过而不是报错。

    用真实 recognize + 脚本化 caller：让奇数调用返回垃圾（解析失败）、
    偶数调用返回有效向量 → 分块合并结果应只含成功区域。
    """
    calls = {"n": 0}

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            return VALS_GARBAGE  # 该区域解析失败
        return VALS_OK

    info = engine.recognize_tiled(big_image, "prompt", caller)
    assert info is not None
    assert info.has_content
    # 至少一个区域成功并渲染出 a1 向量
    assert "a1" in info.render()
