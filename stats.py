"""统计图 → 数据表（4.3）：柱状/折线/饼图的结构化提取与自洽校验。

独立于 figure.py 的解析/校验链路：类目 + 系列数值，带「长度对齐 / 百分比求和 / 非负」
确定性校验；复用 engine 的重试哲学（贪心主调用 + 校验不过采样重试）。

用法（由 CLI / MCP / 脚本注入 caller，签名与 engine.recognize 一致）：
    from vision_kit.stats import recognize_stats
    info = recognize_stats(image_path, client._caller, client.max_tokens)
"""

from __future__ import annotations

import logging

from .engine import MAX_ATTEMPTS, MAX_TOKENS, TEMP_MAIN, TEMP_RETRY
from .figure import _extract_json

logger = logging.getLogger(__name__)

STATS_PROMPT = (
    "这是一张统计图表（柱状图/折线图/饼图/散点图等）。请仔细、逐项读出图中的全部数据：\n"
    "1. categories：横轴类别（或饼图扇区名），按从左到右/从上到下顺序完整列出，不得遗漏；\n"
    "2. series：每条数据系列 {name, values}，values 与 categories **一一对应、长度必须一致**；\n"
    "3. 百分比类数据（饼图/占比图）所有系列之和应 ≈ 100%。\n"
    "然后只输出一个 JSON 对象，不要任何其他文字：\n"
    "{\n"
    '  "type": "柱状图/折线图/饼图/散点图",\n'
    '  "categories": ["一月", "二月", "三月"],\n'
    '  "series": [{"name": "销量", "values": [120, 150, 98]}],\n'
    '  "note": "图表标题、坐标轴说明、单位"\n'
    "}\n"
    "严格要求：\n"
    "- categories 与每个 series.values 长度必须一致，漏一个都算错，必须补齐再输出。\n"
    "- 数值是纯数字，不要出现字母占位（单位放入 note）。\n"
    "- **若图中没有任何数据标注，categories 与 series 输出空数组，严禁编造数据。**"
)

STATS_RETRY_HINT = (
    "\n注意：你前一次回答的统计数据不自洽（系列长度与类别数不一致，"
    "或百分比之和偏离 100%）。请重新逐项数清楚每个类别与每个数值，全部列出。"
)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_stats(text: str) -> tuple[str, str, list[str], list[dict]]:
    """从模型输出解析 (type, note, categories, series)。JSON 优先、失败返回空。"""
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return ("", "", [], [])
    type_ = obj.get("type", "")
    note = obj.get("note", "")
    categories: list[str] = []
    cats = obj.get("categories")
    if isinstance(cats, list):
        categories = [
            str(c) for c in cats if isinstance(c, (str, int, float)) and not isinstance(c, bool)
        ]
    series: list[dict] = []
    sers = obj.get("series")
    if isinstance(sers, list):
        for s in sers:
            if not isinstance(s, dict):
                continue
            vals = s.get("values")
            if not (
                isinstance(vals, list)
                and vals
                and all(
                    isinstance(x, (int, float)) and not isinstance(x, bool) for x in vals
                )
            ):
                continue
            series.append({"name": str(s.get("name", "")), "values": [float(x) for x in vals]})
    return (str(type_), str(note), categories, series)


# ---------------------------------------------------------------------------
# 自洽校验（纯逻辑，表驱动）
# ---------------------------------------------------------------------------


def run_stats_checks(type_: str, categories: list[str], series: list[dict]) -> list[dict]:
    """统计自洽校验：长度对齐 / 百分比求和 / 非负。返回 check 列表。"""
    checks: list[dict] = []
    if categories and series:
        bad = [s["name"] for s in series if len(s["values"]) != len(categories)]
        checks.append(
            {
                "rule": "series_align",
                "passed": not bad,
                "detail": (
                    f"全部系列与类别数({len(categories)})一致"
                    if not bad
                    else f"系列 {', '.join(bad)} 长度与类别数({len(categories)})不一致"
                ),
            }
        )
    if len(series) == 1 and categories:
        vals = series[0]["values"]
        if vals and all(0 < v <= 100 for v in vals) and any(
            k in type_ for k in ("饼", "占比", "百分比")
        ):
            total = sum(vals)
            ok = abs(total - 100.0) <= 1.0
            detail = f"百分比之和 = {total:g}，预期 100"
            if not ok:
                detail += f"，误差 {total - 100:+.1f}"
            checks.append({"rule": "percent_sum", "passed": ok, "detail": detail})
    for s in series:
        bad = [i for i, v in enumerate(s["values"]) if v < 0]
        if bad:
            checks.append(
                {
                    "rule": "nonneg",
                    "passed": False,
                    "detail": f"系列 {s['name'] or '?'} 出现负值（下标 {bad}）",
                }
            )
    if not checks:
        checks.append(
            {
                "rule": "data_present",
                "passed": bool(categories or series),
                "detail": "解析到统计数据" if (categories or series) else "无统计数据",
            }
        )
    return checks


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class StatsData:
    """统计图抽取结果：categories + series + 校验。"""

    def __init__(self, text: str):
        self.raw_text = text
        self.type, self.note, self.categories, self.series = parse_stats(text)
        self.checks = run_stats_checks(self.type, self.categories, self.series)

    @property
    def has_content(self) -> bool:
        return bool(self.categories or self.series)

    @property
    def consistent(self) -> bool:
        return all(c["passed"] for c in self.checks)

    def render(self) -> str:
        parts = []
        if self.type:
            parts.append(f"图表类型：{self.type}")
        if self.categories:
            parts.append("类别：" + "、".join(self.categories))
        for s in self.series:
            vals = ", ".join(_fmt(x) for x in s["values"])
            parts.append(f"{s['name'] or '?'}=[{vals}]")
        for c in self.checks:
            mark = "✓" if c["passed"] else "✗"
            parts.append(f"{mark} [{c['rule']}] {c['detail']}")
        if self.note:
            parts.append(self.note)
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "categories": list(self.categories),
            "series": [
                {"name": s["name"], "values": _num_list(s["values"])} for s in self.series
            ],
            "note": self.note,
            "checks": self.checks,
            "consistent": self.consistent,
            "text": self.render(),
            "raw": self.raw_text.strip(),
        }


# ---------------------------------------------------------------------------
# 重试驱动（与 engine.recognize 同哲学）
# ---------------------------------------------------------------------------


def recognize_stats(
    image_path: str,
    caller,
    max_tokens: int = MAX_TOKENS,
    max_attempts: int = MAX_ATTEMPTS,
) -> StatsData | None:
    """贪心主调用 + 校验不过采样重试；全部失败返回 None（可回退原始文本）。"""
    best: StatsData | None = None
    for attempt in range(max_attempts):
        p = STATS_PROMPT + (STATS_RETRY_HINT if attempt > 0 else "")
        if attempt == 0:
            raw = caller(image_path, p, TEMP_MAIN, False, max_tokens)
        else:
            raw = caller(image_path, p, TEMP_RETRY, True, max_tokens)
        data = StatsData(raw)
        if data.has_content and data.consistent:
            return data
        if data.has_content and best is None:
            best = data
        if not data.has_content and (data.type or data.note):
            return data  # 合法空结果（图中无数据标注），不触发重试
        if not data.render():
            logger.warning("统计识别第 %d 次无返回内容", attempt + 1)
    if best is not None:
        logger.warning("统计校验未通过，返回兜底结果（可能漏数）")
    return best


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _fmt(x: float) -> str:
    return str(int(x)) if x.is_integer() else str(x)


def _num_list(v: list[float]) -> list:
    return [int(x) if x.is_integer() else x for x in v]
