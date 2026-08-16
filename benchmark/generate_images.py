"""benchmark/generate_images.py — 生成基准题图 + ground_truth.jsonl（确定性，无需任何 API）。

用法:
    python benchmark/generate_images.py              # 主集（12 张）+ 对抗集 + 困难集
    python benchmark/generate_images.py --main       # 只生成主集
    python benchmark/generate_images.py --adversarial  # 只生成对抗注入集（矛盾标注）
    python benchmark/generate_images.py --hard       # 只生成困难集（扰动）

输出:
    benchmark/images/<id>.png                    12 张合成题图（主集）
    benchmark/images/adv/<id>_adv.png            6 张对抗注入图（矛盾标注，校验层必须抓出）
    benchmark/images/hard/<id>_hard01.png        12 张困难图（缩放/噪声/JPEG/旋转/低对比度扰动）
    benchmark/ground_truth.jsonl                 主集 GT
    benchmark/ground_truth.adv.jsonl             对抗集 GT（带 expect_error 期望错误码）
    benchmark/ground_truth.hard.jsonl            困难集 GT（与主集数值一致）

设计要点：
- 全确定性：固定坐标 / 数值 / 扰动种子，重复运行输出完全一致；
- 图上标注与 ground truth 逐字一致（数值即标注），便于评测"读得准不准"；
- 数值以「图例块」+ 图内标注双通道呈现，复用几何自洽校验（segment_length / triangle_sum
  等规则在 GT 上天然全部通过，若引擎读错会立刻被抓出）；
- 对抗集：标注与几何自洽矛盾（三角内角和≠180 / 三角不等式违反 / 勾股不成立 /
  向量加法近失 / 矩阵维度不齐 / 圆上点到圆心距离≠半径）→ 校验层必须报错；
- 图内仅使用 ASCII 标注（点坐标、向量分量、角度值用 ∠A=...° 形式），
  依赖系统字体能渲染 ∠ / °（Windows msyh/arial、Linux DejaVu 均可）。
"""

import argparse
import io
import json
import math
import os
import random

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from vision_kit.figure import load_font

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "images")
ADV_DIR = os.path.join(IMG_DIR, "adv")
HARD_DIR = os.path.join(IMG_DIR, "hard")
GT_PATH = os.path.join(HERE, "ground_truth.jsonl")
GT_ADV_PATH = os.path.join(HERE, "ground_truth.adv.jsonl")
GT_HARD_PATH = os.path.join(HERE, "ground_truth.hard.jsonl")

W, H = 900, 700
MARGIN = 80


# ---------------------------------------------------------------------------
# 画布 / 坐标工具
# ---------------------------------------------------------------------------


def _canvas():
    img = Image.new("RGB", (W, H), "white")
    return img, ImageDraw.Draw(img)


def _setup(points, pad: float = 1.5):
    """画网格 + 坐标轴，返回 (img, draw, to_xy, font)。points 决定坐标范围。

    points 可为坐标列表 [[x,y], ...] 或 {name: [x,y]} 字典。
    """
    img, draw = _canvas()
    font = load_font(26)
    coords = list(points.values()) if isinstance(points, dict) else list(points)
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    lo_x, hi_x = min(xs), max(xs)
    lo_y, hi_y = min(ys), max(ys)
    span_x = hi_x - lo_x or 2.0
    span_y = hi_y - lo_y or 2.0
    cx = (lo_x + hi_x) / 2
    cy = (lo_y + hi_y) / 2
    half = max(span_x, span_y) / 2 * pad

    def to_xy(x: float, y: float):
        px = W / 2 + (x - cx) / half * (W / 2 - MARGIN)
        py = H / 2 - (y - cy) / half * (H / 2 - MARGIN)
        return (px, py)

    # 整数网格线
    for xi in range(math.floor(cx - half), math.ceil(cx + half) + 1):
        px, _ = to_xy(xi, cy)
        draw.line([(px, MARGIN), (px, H - MARGIN)], fill=(235, 235, 235), width=1)
    for yi in range(math.floor(cy - half), math.ceil(cy + half) + 1):
        _, py = to_xy(cx, yi)
        draw.line([(MARGIN, py), (W - MARGIN, py)], fill=(235, 235, 235), width=1)
    # 坐标轴（过 0）
    ax0, ay0 = to_xy(0, 0)
    draw.line([(MARGIN, ay0), (W - MARGIN, ay0)], fill="black", width=1)
    draw.line([(ax0, MARGIN), (ax0, H - MARGIN)], fill="black", width=1)
    return img, draw, to_xy, font


def _legend(draw, lines, font=None):
    """底部白底图例块，写多行数值标注（与 ground truth 逐字一致）。"""
    font = font or load_font(24)
    box_h = len(lines) * 32 + 16
    draw.rectangle([(0, H - box_h), (W, H)], fill="white")
    y = H - box_h + 10
    for line in lines:
        draw.text((MARGIN, y), line, fill="black", font=font)
        y += 32


def _arrow(draw, p0, p1, head: int = 12) -> None:
    draw.line([p0, p1], fill="green", width=3)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    ux, uy = dx / length, dy / length
    vx, vy = -uy, ux
    tip = (p1[0] - ux * head, p1[1] - uy * head)
    b1 = (tip[0] + vx * head * 0.5, tip[1] + vy * head * 0.5)
    b2 = (tip[0] - vx * head * 0.5, tip[1] - vy * head * 0.5)
    draw.polygon([p1, b1, b2], fill="green")


def _fmt(v: float) -> str:
    """一位小数，去掉尾零：63.4 / 45.0 -> 45。"""
    s = f"{v:.1f}"
    return s.rstrip("0").rstrip(".")


def _angle_at(a, b, c) -> float:
    """∠bac 的度数。"""
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - a[0], c[1] - a[1])
    d1 = math.hypot(*v1)
    d2 = math.hypot(*v2)
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (d1 * d2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _dist(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


# ---------------------------------------------------------------------------
# 各图生成器
# ---------------------------------------------------------------------------


def _gen_triangle(entries, iid, a, b, c, note, out_dir=IMG_DIR, angle_overrides=None, length_overrides=None):
    """通用三角形图：坐标 / 边长 / 角均进图例，GT 与图例一致。

    angle_overrides / length_overrides（可选 dict）用于对抗集：让图上标注与
    坐标计算值矛盾（如三角内角和 ≠ 180、边长不满足三角形不等式），
    校验层应能抓出（expect_error 由调用方写入 GT）。
    """
    img, draw, to_xy, font = _setup([a, b, c])
    pts = {"A": a, "B": b, "C": c}
    draw.polygon([to_xy(*p) for p in (a, b, c)], outline="black", width=2)
    for name, p in pts.items():
        x, y = to_xy(*p)
        r = 5
        draw.ellipse([x - r, y - r, x + r, y + r], fill="black", outline="black")
        draw.text((x + 6, y - 14), f"{name}({_fmt(p[0])},{_fmt(p[1])})", fill="black", font=font)

    ab = _dist(a, b)
    ac = _dist(a, c)
    bc = _dist(b, c)
    ang_a = _angle_at(a, b, c)
    ang_b = _angle_at(b, a, c)
    ang_c = _angle_at(c, a, b)
    # 对抗覆盖：图上标注用 override 值（与坐标计算值矛盾）
    if length_overrides:
        ab = length_overrides.get("AB", ab)
        ac = length_overrides.get("AC", ac)
        bc = length_overrides.get("BC", bc)
    if angle_overrides:
        ang_a = angle_overrides.get("A", ang_a)
        ang_b = angle_overrides.get("B", ang_b)
        ang_c = angle_overrides.get("C", ang_c)
    # 边长中点标注
    for p, q, val, off in (
        (a, b, ab, (0, -14)),
        (a, c, ac, (6, 0)),
        (b, c, bc, (-6, 0)),
    ):
        mx, my = to_xy((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        draw.text((mx + off[0], my + off[1]), f"{_fmt(val)}", fill="blue", font=font)

    _legend(
        draw,
        [
            f"A({_fmt(a[0])},{_fmt(a[1])})  B({_fmt(b[0])},{_fmt(b[1])})  C({_fmt(c[0])},{_fmt(c[1])})",
            f"AB={_fmt(ab)}  AC={_fmt(ac)}  BC={_fmt(bc)}",
            f"∠A={_fmt(ang_a)}°  ∠B={_fmt(ang_b)}°  ∠C={_fmt(ang_c)}°",
        ],
    )
    path = os.path.join(out_dir, f"{iid}.png")
    img.save(path, format="PNG")
    entries.append(
        {
            "id": iid,
            "image": f"images/{os.path.relpath(path, IMG_DIR)}",
            "type": "几何图",
            "vectors": {},
            "matrices": {},
            "figures": {
                "points": {"A": a, "B": b, "C": c},
                "segments": [
                    {"endpoints": ["A", "B"], "length": round(ab, 1)},
                    {"endpoints": ["A", "C"], "length": round(ac, 1)},
                    {"endpoints": ["B", "C"], "length": round(bc, 1)},
                ],
                "angles": [
                    {"vertex": "A", "sides": ["AB", "AC"], "value": round(ang_a, 1)},
                    {"vertex": "B", "sides": ["BA", "BC"], "value": round(ang_b, 1)},
                    {"vertex": "C", "sides": ["CA", "CB"], "value": round(ang_c, 1)},
                ],
            },
            "note": note,
        }
    )


def _gen_vectors(entries, iid, vecs, tails, note, out_dir=IMG_DIR):
    """通用向量图：分量式 [dx, dy]（与 geometry.vector_sum 的求和语义一致）。

    图例列出 name=(dx,dy)；按 tails 画箭头（如平行四边形法则 a2 从 a1 终点起画）。
    GT 为 2 元素分量 {name: [dx, dy]}。
    """
    pts = {}
    for name, (dx, dy) in vecs.items():
        tx, ty = tails[name]
        pts[f"{name}.s"] = [tx, ty]
        pts[f"{name}.e"] = [tx + dx, ty + dy]
    img, draw, to_xy, font = _setup(pts)
    for name, (dx, dy) in vecs.items():
        tx, ty = tails[name]
        _arrow(draw, to_xy(tx, ty), to_xy(tx + dx, ty + dy))
        mx, my = to_xy(tx + dx / 2, ty + dy / 2)
        draw.text((mx + 6, my - 16), name, fill="green", font=font)
    _legend(draw, [f"{name}=({dx},{dy})" for name, (dx, dy) in vecs.items()])
    path = os.path.join(out_dir, f"{iid}.png")
    img.save(path, format="PNG")
    entries.append(
        {
            "id": iid,
            "image": f"images/{os.path.relpath(path, IMG_DIR)}",
            "type": "向量图",
            "vectors": {name: [dx, dy] for name, (dx, dy) in vecs.items()},
            "matrices": {},
            "note": note,
        }
    )


def _gen_matrix(entries, iid, name, mat, note, out_dir=IMG_DIR):
    """矩阵图：大字号文本块。"""
    img, draw = _canvas()
    font = load_font(34)
    lines = [f"{name} = ["]
    for row in mat:
        lines.append("  [" + ", ".join(str(x) for x in row) + "]")
    lines.append("]")
    draw.multiline_text((80, 160), "\n".join(lines), fill="black", font=font, spacing=10)
    draw.text(
        (80, H - 80), f"{name} 是 {len(mat)}x{len(mat[0])} 矩阵", fill="black", font=load_font(24)
    )
    path = os.path.join(out_dir, f"{iid}.png")
    img.save(path, format="PNG")
    entries.append(
        {
            "id": iid,
            "image": f"images/{os.path.relpath(path, IMG_DIR)}",
            "type": "矩阵图",
            "vectors": {},
            "matrices": {name: mat},
            "note": note,
        }
    )


def _gen_matrix_pair(entries, iid, m1, m2, note, out_dir=IMG_DIR):
    """双矩阵图（对抗集用）：两个矩阵形状不同 → 维度不一致，校验层必须抓出 DIMENSION_MISMATCH。"""
    img, draw = _canvas()
    font = load_font(30)
    lines = []
    for name, mat in (("A", m1), ("B", m2)):
        lines.append(f"{name} = [")
        for row in mat:
            lines.append("  [" + ", ".join(str(x) for x in row) + "]")
        lines.append("]")
        lines.append("")
    draw.multiline_text((80, 140), "\n".join(lines), fill="black", font=font, spacing=8)
    path = os.path.join(out_dir, f"{iid}.png")
    img.save(path, format="PNG")
    entries.append(
        {
            "id": iid,
            "image": f"images/{os.path.relpath(path, IMG_DIR)}",
            "type": "矩阵图",
            "vectors": {},
            "matrices": {"A": m1, "B": m2},
            "note": note,
        }
    )


def _gen_circle(entries, iid, center, radius, on_pts, note, out_dir=IMG_DIR):
    """圆：圆心 / 半径 / 圆上点，坐标标注在图例。"""
    pts = {"O": list(center), **{k: list(v) for k, v in on_pts.items()}}
    img, draw, to_xy, font = _setup(list(pts.values()))
    ox, oy = to_xy(*center)
    # 半径换算像素：沿 x 轴偏移 1 单位
    x1, _ = to_xy(center[0] + 1.0, center[1])
    r_px = abs(x1 - ox) * radius
    draw.ellipse([ox - r_px, oy - r_px, ox + r_px, oy + r_px], outline="black", width=2)
    # 半径线 + 圆上点
    for name, p in on_pts.items():
        px, py = to_xy(*p)
        draw.line([(ox, oy), (px, py)], fill="blue", width=1)
        r = 5
        draw.ellipse([px - r, py - r, px + r, py + r], fill="black", outline="black")
        draw.text((px + 6, py - 14), f"{name}({_fmt(p[0])},{_fmt(p[1])})", fill="black", font=font)
    draw.ellipse([ox - 4, oy - 4, ox + 4, oy + 4], fill="black", outline="black")
    draw.text((ox + 6, oy - 14), f"O({_fmt(center[0])},{_fmt(center[1])})", fill="black", font=font)
    draw.text((ox + r_px * 0.6, oy - r_px - 16), f"r={_fmt(radius)}", fill="blue", font=font)
    _legend(draw, [f"O({_fmt(center[0])},{_fmt(center[1])})  r={_fmt(radius)}"])
    path = os.path.join(out_dir, f"{iid}.png")
    img.save(path, format="PNG")
    on_names = list(on_pts.keys())
    circ = {"center": "O", "radius": radius}
    if on_names:
        circ["on"] = on_names
    entries.append(
        {
            "id": iid,
            "image": f"images/{os.path.relpath(path, IMG_DIR)}",
            "type": "几何图",
            "vectors": {},
            "matrices": {},
            "figures": {"points": pts, "circles": [circ]},
            "note": note,
        }
    )


def _gen_points(entries, iid, pts, note, curve=None, out_dir=IMG_DIR):
    """坐标点图（可选函数曲线）。"""
    img, draw, to_xy, font = _setup(list(pts.values()))
    if curve is not None:
        xs = [curve[0] + i * curve[2] for i in range(curve[3])]
        draw.line([to_xy(x, curve[1](x)) for x in xs], fill="black", width=2)
    for name, p in pts.items():
        x, y = to_xy(*p)
        r = 5
        draw.ellipse([x - r, y - r, x + r, y + r], fill="black", outline="black")
        draw.text((x + 6, y - 14), f"{name}({_fmt(p[0])},{_fmt(p[1])})", fill="black", font=font)
    _legend(draw, [f"{name}({_fmt(p[0])},{_fmt(p[1])})" for name, p in pts.items()])
    path = os.path.join(out_dir, f"{iid}.png")
    img.save(path, format="PNG")
    entries.append(
        {
            "id": iid,
            "image": f"images/{os.path.relpath(path, IMG_DIR)}",
            "type": "函数图" if curve is not None else "几何图",
            "vectors": {},
            "matrices": {},
            "figures": {"points": pts},
            "note": note,
        }
    )


# ---------------------------------------------------------------------------
# 困难集扰动（B2）：纯 PIL，无 numpy；固定种子保证确定性
# ---------------------------------------------------------------------------


def _perturb(img: Image.Image, seed: int) -> Image.Image:
    """按种子确定性施加一种扰动：缩小+JPEG / 旋转+低对比度 / 模糊+缩小+JPEG。"""
    rng = random.Random(seed)
    kind = rng.randrange(3)
    if kind == 0:  # 缩小 + JPEG 压缩（模拟小图/网络压缩图）
        scale = rng.uniform(0.55, 0.75)
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS
        )
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=55)
        img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    elif kind == 1:  # 旋转 2-5° + 对比度降低（模拟扫描倾斜 / 复印失真）
        deg = rng.uniform(2.0, 5.0) * (1 if rng.random() < 0.5 else -1)
        img = img.rotate(deg, resample=Image.BICUBIC, expand=True, fillcolor="white")
        img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.7, 0.85))
    else:  # 高斯模糊 + 缩小 + JPEG（模拟低分辨率 / 模糊拍摄）
        img = img.filter(ImageFilter.GaussianBlur(radius=1.2))
        scale = rng.uniform(0.6, 0.8)
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS
        )
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=50)
        img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return img


def generate_hard(main_entries: list[dict]) -> None:
    """困难集：对每张主集图施加确定性扰动（缩放/噪声/JPEG/旋转/低对比度），
    GT 与主集数值一致（扰动不改内容）——读得准不准、补漏重试的价值在此显现。
    """
    os.makedirs(HARD_DIR, exist_ok=True)
    out_entries: list[dict] = []
    for e in main_entries:
        src = os.path.join(IMG_DIR, os.path.basename(e["image"]))
        with Image.open(src).convert("RGB") as img:
            perturbed = _perturb(img, seed=sum(ord(c) for c in e["id"]))
        iid = f"{e['id']}_hard01"
        perturbed.save(os.path.join(HARD_DIR, f"{iid}.png"), "PNG")
        entry = {k: v for k, v in e.items() if k != "image"}
        entry["id"] = iid
        entry["image"] = f"images/hard/{iid}.png"
        out_entries.append(entry)
    with open(GT_HARD_PATH, "w", encoding="utf-8") as f:
        for entry in out_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"生成 {len(out_entries)} 张困难图 -> {HARD_DIR}")
    print(f"ground truth -> {GT_HARD_PATH}")


# ---------------------------------------------------------------------------
# 对抗注入集（B1）：标注与几何自洽矛盾，校验层必须抓出
# ---------------------------------------------------------------------------

# 每张对抗图的期望防护结果（校验层应报的 error_code）
_ADV_EXPECT = {
    "tri_angles_adv": "GEOMETRY_INCONSISTENT",  # 三角内角和 = 60+60+70 = 190 ≠ 180
    "tri_sides_adv": "GEOMETRY_INCONSISTENT",   # 三边 3/4/9 违反三角形不等式
    "tri_right_adv": "GEOMETRY_INCONSISTENT",   # 直角声明 + 三边 3/4/6.5 违反勾股
    "vec_para_adv": "GEOMETRY_INCONSISTENT",    # a1+a2=(3,4) vs b=(3,4.4) 近失
    "mat_pair_adv": "DIMENSION_MISMATCH",       # A 2x2 vs B 2x3 维度不齐
    "circ_adv": "GEOMETRY_INCONSISTENT",        # 圆上点 P 距圆心 4 vs 半径 3
}


def generate_adversarial() -> None:
    """对抗注入集：在合成图上标注与几何自洽相矛盾的数值，
    VLM 读出即不自洽 → 校验层必须抓出（error_code ≠ OK 或触发重试）。
    """
    os.makedirs(ADV_DIR, exist_ok=True)
    entries: list[dict] = []

    # 1) 三角内角和 = 190 ≠ 180
    _gen_triangle(
        entries, "tri_angles_adv", (0, 0), (6, 0), (2, 4),
        "对抗样本：三角内角和标注 190°（应为 180°）",
        out_dir=ADV_DIR,
        angle_overrides={"A": 60, "B": 60, "C": 70},
    )
    # 2) 三边 3/4/9 违反三角形不等式（最长边 ≥ 另两边之和）
    _gen_triangle(
        entries, "tri_sides_adv", (0, 0), (5, 0), (3.8, 5.879),
        "对抗样本：三边 3/4/9 不满足三角形不等式",
        out_dir=ADV_DIR,
        length_overrides={"AB": 3, "AC": 4, "BC": 9},
    )
    # 3) 直角声明 + 三边 3/4/6.5 违反勾股定理
    _gen_triangle(
        entries, "tri_right_adv", (0, 0), (4, 0), (0, 3.2),
        "对抗样本：直角三角形三边 3/4/6.5 不满足勾股定理",
        out_dir=ADV_DIR,
        angle_overrides={"A": 90},
        length_overrides={"AB": 3, "AC": 4, "BC": 6.5},
    )
    # 4) 向量加法近失：a1+a2=(3,4) 但 b 标注 (3,4.4)
    _gen_vectors(
        entries, "vec_para_adv",
        {"a1": (2, 1), "a2": (1, 3), "b": (3, 4.4)},
        {"a1": (0, 0), "a2": (2, 1), "b": (0, 0)},
        "对抗样本：a1 + a2 应 = b，但 b 的 y 分量标 4.4 而非 4",
        out_dir=ADV_DIR,
    )
    # 5) 矩阵维度不齐：A 2x2、B 2x3
    _gen_matrix_pair(
        entries, "mat_pair_adv",
        [[2, -1], [1, 4]],
        [[1, 2, 3], [4, 5, 6]],
        "对抗样本：A 是 2x2、B 是 2x3，同组矩阵维度不一致",
        out_dir=ADV_DIR,
    )
    # 6) 圆上点到圆心距离 ≠ 半径：O(0,0) r=3，P=(4,0)
    _gen_circle(
        entries, "circ_adv", (0, 0), 3.0, {"P": (4, 0)},
        "对抗样本：P 距圆心 4 而半径标 3",
        out_dir=ADV_DIR,
    )

    with open(GT_ADV_PATH, "w", encoding="utf-8") as f:
        for e in entries:
            e["expect_error"] = _ADV_EXPECT[e["id"]]
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"生成 {len(entries)} 张对抗注入图 -> {ADV_DIR}")
    print(f"ground truth -> {GT_ADV_PATH}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def generate_main() -> list[dict]:
    """主集：12 张合成题图（向量 / 几何三角 / 矩阵 / 圆 / 函数）。返回 GT 条目列表。"""
    os.makedirs(IMG_DIR, exist_ok=True)
    entries: list[dict] = []

    # 向量图（平行四边形法则 / 加法），分量式 [dx, dy]，b = a1 + a2
    _gen_vectors(
        entries,
        "vec_para_01",
        {"a1": (2, 1), "a2": (1, 3), "b": (3, 4)},
        {"a1": (0, 0), "a2": (2, 1), "b": (0, 0)},
        "平行四边形法则：a1 + a2 = b，求向量 b",
    )
    _gen_vectors(
        entries,
        "vec_grid_01",
        {"a": (3, 2), "b": (1, 3), "c": (4, 5)},
        {"a": (0, 0), "b": (0, 0), "c": (0, 0)},
        "向量加法：a + b = c",
    )

    # 几何三角（坐标 / 边长 / 角，三角内角和 = 180）
    _gen_triangle(entries, "tri_angles_01", (0, 0), (6, 0), (2, 4), "三角形 ABC，求三个内角")
    _gen_triangle(entries, "tri_right_01", (0, 0), (4, 0), (0, 3), "直角三角形，A 为直角，求各角")
    _gen_triangle(
        entries, "tri_sides_01", (0, 0), (5, 0), (3.8, 5.879), "已知三边 AB=5, AC=7, BC=6"
    )
    _gen_triangle(entries, "tri_angles_02", (0, 0), (4, 0), (1, 3), "三角形 ABC，求三个内角")

    # 矩阵
    _gen_matrix(entries, "mat_2x2_01", "A", [[2, -1], [1, 4]], "2x2 矩阵 A")
    _gen_matrix(entries, "mat_3x3_01", "B", [[1, 2, 3], [4, 5, 6], [7, 8, 9]], "3x3 矩阵 B")

    # 圆
    _gen_circle(
        entries, "circ_01", (0, 0), 3.0, {"P": (3, 0), "Q": (0, 3)}, "圆心 O，半径 3，P、Q 在圆上"
    )

    # 坐标点 / 函数
    _gen_points(entries, "coord_pts_01", {"A": (1, 2), "B": (4, 5), "C": (-1, 3)}, "求三点坐标")
    _gen_points(
        entries,
        "grid_para_01",
        {"A": (-2, 2), "O": (0, 0), "B": (2, 2)},
        "抛物线 y = x^2 / 2，A、O、B 为图上三点",
        curve=(-2.5, lambda x: x * x / 2, 0.1, 51),
    )

    # 向量 + 矩阵组合
    img, draw, to_xy, font = _setup({"a.s": [0, 0], "a.e": [2, 2]})
    _arrow(draw, to_xy(0, 0), to_xy(2, 2))
    draw.text(to_xy(1, 1.1), "a", fill="green", font=font)
    mfont = load_font(30)
    draw.multiline_text((560, 120), "M = [[1,2],[3,4]]", fill="black", font=mfont, spacing=8)
    _legend(draw, ["a=(2,2)", "M = [[1,2],[3,4]]"])
    path = os.path.join(IMG_DIR, "vec_matrix_combo_01.png")
    img.save(path, format="PNG")
    entries.append(
        {
            "id": "vec_matrix_combo_01",
            "image": "images/vec_matrix_combo_01.png",
            "type": "向量图",
            "vectors": {"a": [2, 2]},
            "matrices": {"M": [[1, 2], [3, 4]]},
            "note": "向量 a 与矩阵 M",
        }
    )

    with open(GT_PATH, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"生成 {len(entries)} 张基准题图 -> {IMG_DIR}")
    print(f"ground truth -> {GT_PATH}")
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark.generate_images", description="生成基准题图")
    parser.add_argument("--main", action="store_true", help="只生成主集（12 张）")
    parser.add_argument("--adversarial", action="store_true", help="只生成对抗注入集（矛盾标注）")
    parser.add_argument("--hard", action="store_true", help="只生成困难集（扰动）")
    args = parser.parse_args(argv)
    flags = [args.main, args.adversarial, args.hard]
    if not any(flags):
        flags = [True, True, True]
    main_entries: list[dict] = []
    if flags[0]:
        main_entries = generate_main()
    if flags[1]:
        generate_adversarial()
    if flags[2]:
        if not main_entries:
            # 未生成主集时从现有 ground_truth 读取（--hard 单独跑）
            if os.path.exists(GT_PATH):
                with open(GT_PATH, encoding="utf-8") as f:
                    main_entries = [json.loads(line) for line in f if line.strip()]
            else:
                main_entries = generate_main()
        generate_hard(main_entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
