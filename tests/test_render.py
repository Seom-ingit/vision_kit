"""figure.py 的 1.4 渲染回写（render_image）单测 —— 确定性 PNG 输出。"""

import os
import tempfile

from PIL import Image
from vision_kit.figure import FigureData


def _assert_valid_png(path, min_bytes=200):
    assert os.path.exists(path)
    assert os.path.getsize(path) > min_bytes
    with Image.open(path) as img:
        assert img.format == "PNG"
        assert img.size == (400, 400)


def _render_and_check(f: FigureData):
    """渲染到临时 PNG 并在删除前完成校验。"""
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        out = f.render_image(name, size=400)
        assert out == name
        _assert_valid_png(name)
    finally:
        _safe_unlink(name)


def test_render_geometry_dom():
    f = FigureData(
        '{"figures": {"points": {"A": [0, 0], "B": [6, 0], "C": [2, 4]},'
        '"segments": [{"endpoints": ["A", "B"], "length": 6}],'
        '"angles": [{"vertex": "A", "sides": ["AB", "AC"], "value": 63.4}],'
        '"circles": [{"center": "A", "radius": 2}]}}'
    )
    _render_and_check(f)


def test_render_vectors_only():
    f = FigureData('{"a1": [2, 1], "a2": [1, 3]}')
    _render_and_check(f)


def test_render_text_only():
    f = FigureData('{"type": "向量图", "note": "只有说明没有数字"}')
    _render_and_check(f)


def test_render_empty_returns_none():
    f = FigureData("totally empty content")
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        assert f.render_image(name) is None
    finally:
        _safe_unlink(name)


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def test_render_deterministic():
    f = FigureData(
        '{"figures": {"points": {"A": [0, 0], "B": [4, 0]},'
        '"segments": [{"endpoints": ["A", "B"], "length": 4}]}}'
    )
    fd1, p1 = tempfile.mkstemp(suffix=".png")
    fd2, p2 = tempfile.mkstemp(suffix=".png")
    os.close(fd1)
    os.close(fd2)
    try:
        f.render_image(p1, size=300)
        f.render_image(p2, size=300)
        with open(p1, "rb") as a, open(p2, "rb") as b:
            assert a.read() == b.read()  # 同一数据两次渲染字节级一致
    finally:
        _safe_unlink(p1)
        _safe_unlink(p2)
