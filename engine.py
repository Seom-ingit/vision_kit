"""识别引擎：图像预处理、参数化调用、贪心+采样补漏重试、超大图分块

配置通过参数/回调传入，不依赖任何宿主项目。
"""

import base64
import io
import logging
import os
import tempfile

from PIL import Image

from . import geometry
from .figure import FigureData

logger = logging.getLogger(__name__)

# 小图/密集数字时放大的目标最小边长
MIN_DIM = 1000
MAX_SCALE = 3.0

MAX_ATTEMPTS = 3

# qwen3-vl-flash 的 max_tokens 上限为 8192（见 opencode.example.json 的 output 限制）；
# 视觉结构化输出通常是短 JSON，8192 足够且留足余量
MAX_TOKENS = 8192

# 主调用贪心（确定性、数字识别最准）；
# 维度不一致需补漏时改用采样（temperature 提高引入多样性）重试
TEMP_MAIN = 0.0
TEMP_RETRY = 0.6

# 超大图分块：单边超过阈值切成 2x2 带重叠的块分别识别
MAX_TILE_SIDE = 1600
TILE_GRID = 2
TILE_OVERLAP = 48

GENERAL_PROMPT = (
    "这是一张图片。请仔细观察，完整、准确、有条理地描述图中的内容："
    "主体对象、文字与数字标注、图表结构、布局与颜色、空间关系等。"
    "若图中包含题目、说明或任何文字，请逐字转写。"
    "只输出对图片本身的描述，不要添加推测或解释。"
)

STRUCTURED_PROMPT = (
    "这是一道学习题目中的图形/示意图。请仔细、逐行核对图中所有带数字的标注"
    "（点坐标、线段长度、角度、半径、向量、矩阵、未知量等），把每个标注对应的数字"
    "**完整列出，逐行逐列数清楚，不得遗漏任何一个数字**。"
    "然后只输出一个 JSON 对象，不要任何其他文字：\n"
    "{\n"
    '  "type": "图形类型（几何图/函数图/电路图/流程图/向量图等）",\n'
    '  "figures": {\n'
    '    "points": {"A": [x, y], "B": [x, y]},\n'
    '    "segments": [{"endpoints": ["A", "B"], "length": 4}],\n'
    '    "angles": [{"vertex": "A", "sides": ["AB", "AC"], "value": 48}],\n'
    '    "circles": [{"center": "O", "radius": 2.5, "on": ["P", "Q"]}],\n'
    '    "quads": [{"vertices": ["A", "B", "C", "D"], "type": "parallelogram"}]\n'
    "  },\n"
    '  "vectors": {"a1": [1, 1, 2, 2], "a2": [1, 2, 1, 3], "b": [1, 0, 2, 3]},\n'
    '  "matrices": {"A": [[2, -1], [1, 4]]},\n'
    '  "note": "图形结构、已知条件与所求量（不要解题，只描述）"\n'
    "}\n"
    "严格要求：\n"
    "- 几何图（有顶点/线段/角/圆）必须把点、线段、角、圆填进 figures；"
    "纯向量/矩阵图可以省略 figures。\n"
    "- 圆必须列出图中位于圆周上的点（on 字段，如 P、Q；图中标出的圆上点都要列入），"
    "否则无法校验『圆上点到圆心距离 = 半径』。\n"
    "- 若图中出现平行四边形/矩形/菱形等特殊四边形，用 quads 声明（vertices 按顺序列 4 个顶点，"
    "type 写平行四边形/矩形/菱形）。\n"
    "- 向量/矩阵必须把每个分量都列出来，遗漏任何一个分量都算错。\n"
    "- 同一题目中相关的向量（如 a1、a2、a3、b）维度必须一致；"
    "若发现某个向量比其他的短，说明漏了数字，必须补齐再输出。\n"
    "- 所有 figures 里引用的点（线段端点、角顶点、圆圆心）必须已在 points 中定义；"
    "线段/角/圆的数值必须与点的坐标自洽（如 |AB| 应等于 A、B 两点距离）。\n"
    "- 分量是纯数字数组，不要出现字母占位（未知量放入 note 描述）。\n"
    "- **若图中没有任何带数字的标注**（无向量、无矩阵、无坐标/角度/长度/半径），"
    "vectors 与 matrices 必须输出 {}，figures 可省略，note 写“图中无数字标注”。"
    "**严禁编造不存在的向量/矩阵/坐标/角度**；不确定的数字宁可标在 note 里也不要硬填。"
)

RETRY_HINT = (
    "\n注意：你前一次的回答遗漏了部分数字（向量维度不一致，或线段端点/角顶点/圆圆心"
    "引用了未定义的点，或标注数值与坐标不自洽）。"
    "请重新从上到下、从左到右逐行数清楚每个向量的所有分量、每个点的坐标与每条线段的长度，"
    "确保引用完整、数值自洽，并全部列出。"
)

# 定向重试：geo_checks 失败规则 → 针对性修复指令（A1，校验层闭环到重试层）。
# 让第 2/3 次采样重试带着「具体错在哪」的提示去读图，而不是笼统地「重新数一遍」。
_RULE_HINTS: dict[str, str] = {
    "triangle_sum": (
        "你前一次读出的三角形内角和不是 180°。请重新逐角核对图中三个角的度数"
        "（∠A/∠B/∠C 或对应顶点），把每个角的数值读准。"
    ),
    "vector_sum": (
        "你前一次读出的向量不满足加法关系（如 a1 + a2 = b）。请重新核对相关向量的"
        "每个分量，特别是作为加法结果的那个向量（最容易读错）。"
    ),
    "segment_length": (
        "你前一次读出的线段标注长度与端点坐标计算的距离不一致。请重新核对所有点的坐标"
        "与所有线段的长度标注。"
    ),
    "triangle_inequality": (
        "你前一次读出的三边不满足三角形不等式（最长边必须小于另两边之和）。"
        "请重新核对三条边的长度标注，最长边尤其容易读错。"
    ),
    "pythagoras": (
        "你前一次读出的直角三角形三边不满足勾股定理（斜边² = 直角边₁² + 直角边₂²）。"
        "请重新核对直角边与斜边的长度标注。"
    ),
    "plausible_range": (
        "你前一次读出的数值存在负值或越界（长度/角度不应为负，角度应在 0~180° 之间）。"
        "请重新核对所有数值标注。"
    ),
    "circle_radius": (
        "你前一次读出的圆上点到圆心的距离与半径不一致。请重新核对圆心坐标、半径与圆上点坐标。"
    ),
    "diameter_right_angle": (
        "你前一次读出的圆内直角与直径关系矛盾（直径所对的圆周角应为 90°）。"
        "请重新核对圆上点坐标与角度标注。"
    ),
    "similar_proportion": (
        "你前一次读出的两个三角形对应边不成比例。请重新核对两组三角形的边长标注。"
    ),
    "parallelogram": (
        "你前一次读出的平行四边形对边不相等。请重新核对四个顶点的坐标与对边的长度标注。"
    ),
    "function_symmetry": (
        "你前一次读出的函数图对称轴与两零点中点不一致。请重新核对零点与顶点的 x 坐标。"
    ),
}

_DIMENSION_HINT = (
    "你前一次回答的向量/矩阵维度不一致（同组向量长度或矩阵形状不齐，可能漏读了分量或行）。"
    "请重新逐行逐列数清每个向量/矩阵的分量个数，把漏掉的数字全部补齐。"
)


def retry_hint_for(info: "FigureData") -> str:
    """按上一次识别的失败原因生成定向重试提示；无已知失败原因返回空串。

    优先级：维度不一致 > 几何失败规则（取前 2 条去重）。映射不到的规则回退通用 RETRY_HINT。
    """
    if info is None:
        return ""
    if not info.consistent:
        return _DIMENSION_HINT
    failed = [c["rule"] for c in info.geo_checks if not c["passed"]]
    if not failed:
        return ""
    hints = []
    for rule in failed:
        hint = _RULE_HINTS.get(rule)
        if hint and hint not in hints:
            hints.append(hint)
        if len(hints) >= 2:
            break
    if not hints:
        return RETRY_HINT
    return "\n注意：" + " ".join(hints) + " 然后重新输出完整 JSON。"


def preprocess(image_path: str) -> str:
    """放大小图/密集图，返回 base64 PNG"""
    try:
        with Image.open(image_path).convert("RGB") as img:
            w, h = img.size
            if max(w, h) < MIN_DIM:
                scale = min(MAX_SCALE, MIN_DIM / max(w, h))
                if scale > 1.0:
                    img = img.resize(
                        (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
                    )
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:  # noqa: BLE001
        logger.warning("图片预处理失败，使用原图: %s", e)
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()


def needs_tiling(image_path: str) -> bool:
    try:
        with Image.open(image_path) as im:
            w, h = im.size
            return max(w, h) > MAX_TILE_SIDE
    except Exception:  # noqa: BLE001
        return False


def recognize(
    image_path: str, prompt: str, caller, max_tokens: int = MAX_TOKENS
) -> FigureData | None:
    """单图识别主流程：贪心主调用 + 维度/几何不一致时采样补漏重试。

    caller 由客户端注入（负责真正调用模型），签名：
        caller(image_path, prompt, temperature, do_sample, max_tokens) -> str
    返回结构化 FigureData；全部失败时返回 None（可回退原始文本）。
    重试耗尽后保留首个有内容的结果兜底，避免丢弃已识别到的内容。

    判定标准（越靠后越"软"）：
      1. 有内容 + 维度一致 + 几何自洽 → 立即返回（最强）
      2. 有内容 + 维度一致但几何不自洽 → 保留并触发采样重试（"抓错"）
      3. 维度不一致 → 保留首个有内容结果并触发补漏重试
    """
    best: FigureData | None = None
    geo_best: FigureData | None = None
    last: FigureData | None = None
    for attempt in range(MAX_ATTEMPTS):
        # 定向重试：把上一次失败的具体原因（维度 / 几何规则）注入重试提示（A1）
        hint = retry_hint_for(last) if attempt > 0 else ""
        p = prompt + hint
        if attempt == 0:
            last_raw = caller(image_path, p, TEMP_MAIN, False, max_tokens)
        else:
            last_raw = caller(image_path, p, TEMP_RETRY, True, max_tokens)
        info = FigureData(last_raw)
        last = info
        if info.has_content and info.consistent and _geo_ok(info):
            info.attempts = attempt + 1
            return info
        if info.has_content:
            # 保留下一次几何可能改善的结果（维度一致优先；否则首个有内容结果）
            if info.consistent and (geo_best is None or not geo_best.consistent):
                geo_best = info
            elif best is None:
                best = info
            continue
        if info.render() and best is None and geo_best is None:
            # 无数值但有 type/note（文本/公式图），且此前无任何数值内容 → 直接返回
            info.attempts = attempt + 1
            return info
    # 维度一致但几何不自洽：返回它并保留 geo_checks 供上层诊断，不静默丢弃
    if geo_best is not None:
        logger.warning("几何自洽校验未通过（可能读数有误）")
        geo_best.attempts = MAX_ATTEMPTS
        return geo_best
    if best is not None and not best.consistent:
        logger.warning("维度校验未通过，返回兜底结果（可能漏数）")
        best.attempts = MAX_ATTEMPTS
    return best


def _geo_ok(info: "FigureData") -> bool:
    """几何自洽判定：含数值时须通过全部几何检查；无数值（纯文本图）视为通过。"""
    if not info.has_content:
        return True
    return geometry.overall_consistent(getattr(info, "geo_checks", []))


def recognize_tiled(
    image_path: str, prompt: str, caller, max_tokens: int = MAX_TOKENS
) -> "FigureData | None":
    """超大图 2x2 分块识别后**合并再校验**（A3）：各块分别走 recognize，
    合并成 FigureData 后重新跑维度/几何校验（跨块向量、跨块几何关系也能被抓出）。
    全部区域失败时返回 None。
    """
    tiles = _split_tiles(image_path)
    parts: list[FigureData] = []
    try:
        for i, t in enumerate(tiles):
            info = recognize(t, prompt, caller, max_tokens)
            if info and info.has_content:
                parts.append(info)
            else:
                # 该区域识别失败：直接跳过（不要把 PNG 二进制当文本解码，那只会得到乱码）
                logger.warning("区域 %d 识别失败，该区域将被跳过", i + 1)
    finally:
        for t in tiles:
            try:
                os.unlink(t)
            except OSError:
                pass
    if not parts:
        return None
    merged = FigureData.merge(parts)
    merged.attempts = max((p.attempts or 1) for p in parts)
    return merged


def describe_tiled(
    image_path: str, prompt: str, caller, max_tokens: int = MAX_TOKENS
) -> str:
    """超大图分块自然语言描述：每块分别用同一 prompt 调用，按区域拼接文本。

    用于 describe 模式：整图描述失败（空结果）且确为超大图时分块兜底，
    避免长截图/大图在单次整图调用下丢失内容。
    """
    tiles = _split_tiles(image_path)
    parts: list[str] = []
    try:
        for i, t in enumerate(tiles):
            text = caller(t, prompt, TEMP_MAIN, False, max_tokens)
            if text and text.strip():
                parts.append(f"【区域 {i + 1}】\n{text.strip()}")
    finally:
        for t in tiles:
            try:
                os.unlink(t)
            except OSError:
                pass
    return "\n\n".join(parts)


def _split_tiles(image_path: str) -> list[str]:
    """超大图切成 2x2 带重叠的块，返回临时 PNG 路径列表"""
    img = Image.open(image_path).convert("RGB")
    try:
        w, h = img.size
        cols = rows = TILE_GRID
        # 向上取整保证所有块拼接后覆盖整图（否则右/下边缘可能漏掉最多 1 像素）
        step_x = max(1, (w - TILE_OVERLAP + cols - 1) // cols)
        step_y = max(1, (h - TILE_OVERLAP + rows - 1) // rows)
        tiles: list[str] = []
        for r in range(rows):
            for c in range(cols):
                x0 = min(c * step_x, w - 1)
                y0 = min(r * step_y, h - 1)
                x1 = min(x0 + step_x + TILE_OVERLAP, w)
                y1 = min(y0 + step_y + TILE_OVERLAP, h)
                tile = img.crop((x0, y0, x1, y1))
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.close()
                tile.save(tmp.name)
                tiles.append(tmp.name)
        return tiles
    finally:
        img.close()
