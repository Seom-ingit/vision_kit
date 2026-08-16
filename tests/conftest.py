"""pytest 共享 fixtures。

本套测试刻意不依赖 pytest 内置的 tmp_path/tmp_path_factory（其在 Windows 受限
沙箱下对临时目录的扫描/清理会被拒绝）。改用 tempfile 在 OS 临时区直接创建图片
文件（该操作在沙箱内可用），并在 fixture teardown 中关闭句柄后删除。
"""

import os
import sys
import tempfile

import pytest
from PIL import Image

# 把仓库根目录加入 sys.path，使 tests 可 import benchmark（namespace 包）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def pytest_addoption(parser):
    """2.4 回归快照：--update-snapshots 重录基线（需要 VISION_API_KEY 真实 API）。"""
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="重录 tests/snapshots/ 回归快照（需要 VISION_API_KEY）",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "snapshot: 回归快照测试（需要 VISION_API_KEY 真实 API，离线自动跳过）",
    )


def _make_image(path, size, fill):
    img = Image.new("RGB", size, fill)
    img.save(path, format="PNG")


@pytest.fixture
def tmp_image():
    """生成一张 32x24 小 PNG（小到会触发 engine.preprocess 的放大）。"""
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    _make_image(name, (32, 24), (255, 255, 255))
    yield name
    _safe_unlink(name)


@pytest.fixture
def big_image():
    """生成一张超过 MAX_TILE_SIDE 的大 PNG（触发分块，但只用临时文件避免长期占用）。"""
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    _make_image(name, (3200, 2000), (255, 255, 255))
    yield name
    _safe_unlink(name)


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass
