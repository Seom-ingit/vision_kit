# -*- coding: utf-8 -*-
"""Generate README showcase images for vision_kit.

Outputs (docs/images/):
  flow.png          四段式闭环流程图（读图→校验→重试→诊断）
  demo_catch.png    对抗演示图：VLM 自信地错 vs vision_kit 拦截
  benchmark.png     开源基准数字卡

Run:  python scripts/make_readme_assets.py
Requires: Pillow + Windows 中文字体（msyh.ttc / simhei.ttf，可改 FONT 路径）
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "images")

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

# ---- palette ----
BG = "#FFFFFF"
INK = "#111827"
SUB = "#6B7280"
BLUE = "#2563EB"
BLUE_BG = "#EFF6FF"
GREEN = "#16A34A"
GREEN_BG = "#F0FDF4"
RED = "#DC2626"
RED_BG = "#FEF2F2"
ORANGE = "#EA580C"
ORANGE_BG = "#FFF7ED"
PURPLE = "#7C3AED"
PURPLE_BG = "#F5F3FF"
GRAY_BG = "#F8FAFC"
BORDER = "#E5E7EB"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def center_text(draw: ImageDraw.Draw, cx: int, y: int, text: str, f: ImageFont.FreeTypeFont, fill: str) -> None:
    bbox = draw.textbbox((0, 0), text, font=f)
    w = bbox[2] - bbox[0]
    draw.text((cx - w / 2, y), text, font=f, fill=fill)


def rounded_box(draw: ImageDraw.Draw, xy, radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def arrow(draw: ImageDraw.Draw, x1: int, y1: int, x2: int, y2: int, color: str = "#9CA3AF", width: int = 3) -> None:
    draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
    # arrowhead
    import math

    ang = math.atan2(y2 - y1, x2 - x1)
    L = 10
    for da in (0.42, -0.42):
        draw.line(
            [(x2, y2), (x2 - L * math.cos(ang - da), y2 - L * math.sin(ang - da))],
            fill=color,
            width=width,
        )


# =====================================================================
# 1) flow.png — 四段式闭环
# =====================================================================
def make_flow() -> str:
    W, H = 1200, 470
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    center_text(d, W // 2, 28, "vision_kit · 四段式闭环：读图 → 校验 → 重试 → 诊断", font(26, True), INK)
    center_text(d, W // 2, 66, "在 VLM 输出之后加一层纯逻辑的确定性校验 —— 让「读错了」变成显式信号", font(15), SUB)

    nodes = [
        ("输入题图", GREEN, GREEN_BG, "数学 / 几何图", "小图自动放大"),
        ("① 结构化提取", BLUE, BLUE_BG, "VLM → 图元 DOM", "点/线段/角/圆/四边形 + 数值"),
        ("② 确定性校验", BLUE, BLUE_BG, "纯逻辑 · 零外部依赖", "维度一致 · 几何自洽 · 引用完整"),
        ("③ 定向补漏重试", ORANGE, ORANGE_BG, "「错在哪」回流成提示", "第 2/3 次采样重读"),
        ("④ 置信度诊断", PURPLE, PURPLE_BG, "错误码 + 置信度", "Agent 可据此行动"),
    ]
    box_w, gap, top, box_h = 208, 18, 118, 88
    total = len(nodes) * box_w + (len(nodes) - 1) * gap
    x0 = (W - total) // 2
    for i, (title, color, bg, line1, line2) in enumerate(nodes):
        x = x0 + i * (box_w + gap)
        rounded_box(d, (x, top, x + box_w, top + box_h), 14, bg, color, 2)
        center_text(d, x + box_w // 2, top + 14, title, font(16, True), color)
        center_text(d, x + box_w // 2, top + 42, line1, font(12), INK)
        center_text(d, x + box_w // 2, top + 62, line2, font(12), SUB)
        if i < len(nodes) - 1:
            arrow(d, x + box_w + 2, top + box_h // 2, x + box_w + gap - 2, top + box_h // 2)

    # bottom result bar
    by = 240
    rounded_box(d, (x0, by, x0 + total, by + 60), 14, GRAY_BG, BORDER, 1)
    center_text(d, W // 2, by + 10, "每条结果带 diagnostics（错误码 + 整体/逐值置信度 + 重试次数）", font(14, True), INK)
    center_text(d, W // 2, by + 36, "✓ / ✗ 逐条返回 → Agent 发现「VLM 读错数」，不把错题当真", font(13), GREEN)

    # failure loop annotation
    loop_y = 340
    center_text(d, W // 2, loop_y, "校验失败 → 不采信输出 → 定向重试（最多 3 次）→ 重试耗尽则如实报告 ✗", font(14), RED)
    # mini loop arrow diagram
    ly = loop_y + 34
    items = ["读图", "校验", "✓ 返回", "✗ 重试"]
    w2 = 90
    sp = 220
    xs = [W // 2 - sp, W // 2 - sp / 3, W // 2 + sp / 3, W // 2 + sp]
    for i, t in enumerate(items):
        color = GREEN if t == "✓ 返回" else (RED if t == "✗ 重试" else BLUE)
        bg = GREEN_BG if t == "✓ 返回" else (RED_BG if t == "✗ 重试" else BLUE_BG)
        rounded_box(d, (xs[i] - w2 // 2, ly, xs[i] + w2 // 2, ly + 40), 10, bg, color, 2)
        center_text(d, xs[i], ly + 10, t, font(13, True), color)
        if i < len(items) - 1:
            arrow(d, xs[i] + w2 // 2 + 4, ly + 20, xs[i + 1] - w2 // 2 - 4, ly + 20)

    path = os.path.join(OUT_DIR, "flow.png")
    img.save(path)
    return path


# =====================================================================
# 2) demo_catch.png — 对抗演示
# =====================================================================
def make_demo() -> str:
    W, H = 1240, 640
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    center_text(d, W // 2, 26, "同一张题图，两种结局", font(28, True), INK)
    center_text(d, W // 2, 66, "对抗样本：图上标注 ∠A=60° ∠B=60° ∠C=70°（内角和 190°，真实三角形应为 180°）", font(15), SUB)

    # ---- center: the triangle (real GT data from tri_angles_adv) ----
    tx, ty, scale = 130, 210, 26  # A(0,0) B(6,0) C(2,4)
    A = (tx + 0 * scale, ty + 0 * scale)
    B = (tx + 6 * scale, ty + 0 * scale)
    C = (tx + 2 * scale, ty + 4 * scale)
    d.line([A, B, C, A], fill=INK, width=3)
    for p, label, dx, dy in ((A, "A", -14, 10), (B, "B", 6, 10), (C, "C", -12, -26)):
        d.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill=RED, outline=RED)
        d.text((p[0] + dx, p[1] + dy), label, font=font(16, True), fill=INK)
    # angle marks (red, inconsistent)
    d.text((A[0] - 66, A[1] + 6), "∠A=60°", font=font(15, True), fill=RED)
    d.text((B[0] + 14, B[1] + 6), "∠B=60°", font=font(15, True), fill=RED)
    d.text((C[0] + 18, C[1] - 34), "∠C=70°", font=font(15, True), fill=RED)
    center_text(d, tx + 3 * scale, ty + 4 * scale + 16, "对抗注入：∠A+∠B+∠C = 190°（≠ 180°）", font(13, True), RED)

    # ---- left panel: plain VLM ----
    lx, ly, pw, ph = 470, 150, 350, 430
    rounded_box(d, (lx, ly, lx + pw, ly + ph), 16, RED_BG, RED, 2)
    center_text(d, lx + pw // 2, ly + 16, "纯 VLM 看图", font(18, True), RED)
    center_text(d, lx + pw // 2, ly + 48, "信任图上标注 → 直接输出", font(13), SUB)
    lines = [
        ("∠A = 60°  ✓", INK),
        ("∠B = 60°  ✓", INK),
        ("∠C = 70°  ✓", INK),
        ("内角和 190°？看起来没问题", SUB),
    ]
    yy = ly + 86
    for t, c in lines:
        d.text((lx + 28, yy), t, font=font(15), fill=c)
        yy += 34
    rounded_box(d, (lx + 24, ly + 240, lx + pw - 24, ly + 300), 10, "#FFFFFF", RED, 1)
    center_text(d, lx + pw // 2, ly + 252, "把错题当真", font(14, True), RED)
    center_text(d, lx + pw // 2, ly + 276, "下游 Agent 在 190° 的三角形上继续推演", font(12), SUB)
    center_text(d, lx + pw // 2, ly + 360, "✗ 无任何失败信号", font(20, True), RED)

    # ---- right panel: vision_kit ----
    rx = lx + pw + 50
    rounded_box(d, (rx, ly, rx + pw, ly + ph), 16, GREEN_BG, GREEN, 2)
    center_text(d, rx + pw // 2, ly + 16, "vision_kit", font(18, True), GREEN)
    center_text(d, rx + pw // 2, ly + 48, "VLM 输出 + 确定性校验层", font(13), SUB)
    lines2 = [
        ("∠A = 60°  ✓   ∠B = 60°  ✓", INK),
        ("∠C = 70°  ✗  GEOMETRY_INCONSISTENT", RED),
        ("内角和 = 190° ≠ 180°", RED),
        ("→ 定向重试：「∠A+∠B+∠C=190°，请复核三个角标注」", INK),
    ]
    yy = ly + 86
    for t, c in lines2:
        d.text((rx + 24, yy), t, font=font(14), fill=c)
        yy += 36
    rounded_box(d, (rx + 24, ly + 250, rx + pw - 24, ly + 310), 10, "#FFFFFF", GREEN, 1)
    center_text(d, rx + pw // 2, ly + 262, "拦截错误，如实报告 ✗", font(14, True), GREEN)
    center_text(d, rx + pw // 2, ly + 286, "Agent 可换模型 / 放大重读 / 放弃作答", font(12), SUB)
    center_text(d, rx + pw // 2, ly + 360, "✓ 错误是显式信号", font(20, True), GREEN)

    path = os.path.join(OUT_DIR, "demo_catch.png")
    img.save(path)
    return path


# =====================================================================
# 3) benchmark.png — 基准数字卡
# =====================================================================
def make_benchmark() -> str:
    W, H = 1240, 380
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    center_text(d, W // 2, 26, "开源基准：12 张合成题图 · qwen3-vl-flash", font(26, True), INK)
    center_text(d, W // 2, 64, "数值级回归快照 · CI 自动比对 · 可复现（benchmark/REPORT.md）", font(15), SUB)

    cards = [
        ("97.2%", "数值准确率", BLUE, BLUE_BG, "35/36 分量"),
        ("0", "幻觉数", GREEN, GREEN_BG, "不把错的当真的"),
        ("100%", "维度/引用一致性", PURPLE, PURPLE_BG, "向量/矩阵/图元 DOM"),
        ("100%", "几何自洽率", ORANGE, ORANGE_BG, "三角/向量/勾股/圆"),
    ]
    cw, gap, top, ch = 260, 40, 120, 150
    total = len(cards) * cw + (len(cards) - 1) * gap
    x0 = (W - total) // 2
    for i, (num, label, color, bg, sub) in enumerate(cards):
        x = x0 + i * (cw + gap)
        rounded_box(d, (x, top, x + cw, top + ch), 16, bg, color, 2)
        center_text(d, x + cw // 2, top + 18, num, font(40, True), color)
        center_text(d, x + cw // 2, top + 78, label, font(16, True), INK)
        center_text(d, x + cw // 2, top + 106, sub, font(12), SUB)

    rounded_box(d, (x0, top + ch + 26, x0 + total, top + ch + 74), 12, GRAY_BG, BORDER, 1)
    center_text(d, W // 2, top + ch + 40, "平均调用 1.0 次（贪心直出即达标，重试是安全网）  ·  平均延迟 3.1s/图", font(14, True), INK)
    center_text(d, W // 2, top + ch + 62, "另设 6 张对抗注入集（矛盾标注）与 12 张困难集（扰动图），量化校验层防护率", font(12), SUB)

    path = os.path.join(OUT_DIR, "benchmark.png")
    img.save(path)
    return path


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    for fn in (make_flow, make_demo, make_benchmark):
        print("saved:", fn())
