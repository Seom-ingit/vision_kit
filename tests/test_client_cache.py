"""VisionClient 内容寻址缓存单测：同图幂等 / 内容变化失效 / 失败不缓存。

与 conftest 一致不依赖 pytest tmp_path（Windows 受限沙箱下临时目录扫描会被拒），
用 tempfile 在 OS 临时区建 PNG；monkeypatch 实例 _caller 计数，不发真实请求。
"""
import os
import tempfile

import pytest
from PIL import Image
from vision_kit import engine
from vision_kit.client import VisionClient

STRUCTURED_OK = '{"a1": [1, 2]}'  # 可解析且维度一致 → recognize 一次调用即返回
NATURAL_TEXT = "这是一张纯色图片，没有任何文字标注。"
GARBAGE = "totally ;; not json"


def _make_image(path, size=(32, 24), fill=(255, 255, 255)):
    Image.new("RGB", size, fill).save(path, format="PNG")


@pytest.fixture
def img_path():
    """生成一张 32x24 小 PNG（内容寻址缓存用同一路径的原始字节哈希）。"""
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    _make_image(name)
    yield name
    try:
        os.unlink(name)
    except OSError:
        pass


def _make_client(**kwargs) -> VisionClient:
    kwargs.setdefault("api_base", "http://localhost:1")
    kwargs.setdefault("api_key", "sk-test")
    kwargs.setdefault("model", "qwen3-vl-flash")
    return VisionClient(**kwargs)


def _patch_caller(client: VisionClient, response: str, calls: list) -> None:
    """把实例 _caller 替换为返回固定文本的计数器（不触发真实 OpenAI 请求）。"""

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        return response

    client._caller = caller  # noqa: SLF001


# ---------------------------------------------------------------------------
# describe_structured：命中 / 内容变化失效 / 失败不缓存
# ---------------------------------------------------------------------------


def test_structured_cached_same_image(img_path):
    calls: list = []
    client = _make_client()
    _patch_caller(client, STRUCTURED_OK, calls)

    first = client.describe_structured(img_path)
    second = client.describe_structured(img_path)

    assert calls == [1]  # 第二次命中缓存，_caller 仍只调 1 次
    assert first == second
    assert first is not None
    assert first["vectors"] == {"a1": [1, 2]}


def test_structured_miss_on_content_change(img_path):
    calls: list = []
    client = _make_client()
    _patch_caller(client, STRUCTURED_OK, calls)

    client.describe_structured(img_path)
    assert len(calls) == 1
    # 图片内容改变（不同像素）→ 哈希变化 → miss，重新走 _caller
    _make_image(img_path, fill=(0, 0, 0))
    client.describe_structured(img_path)
    assert len(calls) == 2


def test_structured_failure_not_cached(img_path):
    """返回垃圾（解析出 None）→ 不缓存，每次调用都重新走 _caller。"""
    calls: list = []
    client = _make_client()
    _patch_caller(client, GARBAGE, calls)

    assert client.describe_structured(img_path) is None
    first_calls = len(calls)
    assert first_calls > 0
    assert client.describe_structured(img_path) is None
    assert len(calls) == first_calls * 2  # 每次都重新走（含 recognize 重试）


def test_structured_clear_cache_evicts(img_path):
    calls: list = []
    client = _make_client()
    _patch_caller(client, STRUCTURED_OK, calls)

    client.describe_structured(img_path)
    assert len(calls) == 1
    client.clear_cache()
    client.describe_structured(img_path)
    assert len(calls) == 2


def test_structured_cache_disabled(img_path):
    calls: list = []
    client = _make_client(cache=False)
    _patch_caller(client, STRUCTURED_OK, calls)

    client.describe_structured(img_path)
    client.describe_structured(img_path)
    assert len(calls) == 2  # 关缓存后每次都调 _caller


def test_structured_evicts_oldest_when_full(img_path):
    calls: list = []
    client = _make_client(cache_max=1)
    _patch_caller(client, STRUCTURED_OK, calls)

    client.describe_structured(img_path)          # 缓存 [img A]
    _make_image(img_path, fill=(0, 0, 0))         # 内容变化 → 新键
    client.describe_structured(img_path)          # 缓存 [img B]，img A 被淘汰
    assert len(calls) == 2
    _make_image(img_path, fill=(255, 255, 255))   # 恢复 img A 的内容
    client.describe_structured(img_path)          # A 已被淘汰 → miss
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# describe_natural：命中 / 空结果不缓存 / 关缓存
# ---------------------------------------------------------------------------


def test_natural_cached_same_image(img_path):
    calls: list = []
    client = _make_client()
    _patch_caller(client, NATURAL_TEXT, calls)

    first = client.describe_natural(img_path)
    second = client.describe_natural(img_path)

    assert calls == [1]
    assert first == NATURAL_TEXT
    assert second == NATURAL_TEXT


def test_natural_empty_not_cached(img_path):
    """返回空串（识别失败）→ 不缓存，每次调用都重新走 _caller。"""
    calls: list = []
    client = _make_client()
    _patch_caller(client, "", calls)

    assert client.describe_natural(img_path) == ""
    assert client.describe_natural(img_path) == ""
    assert len(calls) == 2


def test_natural_exception_not_cached(img_path):
    """_caller 抛异常（模型不可用）→ 返回空串且不缓存，下次重试。"""
    calls: list = []

    def caller(image_path, prompt, temperature, do_sample, max_tokens):
        calls.append(1)
        raise RuntimeError("network down")

    client = _make_client()
    client._caller = caller  # noqa: SLF001

    assert client.describe_natural(img_path) == ""
    assert client.describe_natural(img_path) == ""
    assert len(calls) == 2


def test_natural_cache_disabled(img_path):
    calls: list = []
    client = _make_client(cache=False)
    _patch_caller(client, NATURAL_TEXT, calls)

    client.describe_natural(img_path)
    client.describe_natural(img_path)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 边界：文件读取失败不抛（照常走原逻辑）
# ---------------------------------------------------------------------------


def test_cache_key_read_failure_returns_none(img_path):
    client = _make_client()
    os.unlink(img_path)
    assert client._cache_key(img_path, "natural", engine.GENERAL_PROMPT) is None  # noqa: SLF001
