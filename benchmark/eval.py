"""benchmark/eval.py — 对被测引擎统一跑同一套 prompt 模板，产出 REPORT.md 榜单（2.1）。

用法:
    python benchmark/eval.py [--limit N] [--update-snapshots] [--baseline]
    python benchmark/eval.py --limit 3            # 快速试跑
    python benchmark/eval.py --baseline           # 额外跑单次贪心直出基线，量化校验+重试增益

依赖环境变量（与 CLI 一致）:
    VISION_API_KEY     必填。OpenAI 兼容视觉服务的 API Key
    VISION_API_BASE    可选。默认 https://dashscope.aliyuncs.com/compatible-mode/v1
    VISION_MODEL       可选。默认 qwen3-vl-flash
    VISION_TIMEOUT     可选。默认 60
    VISION_MAX_TOKENS  可选。默认 8192

指标（定义见 benchmark/README.md）:
    数值准确率 / 召回 / 幻觉数 / 一致性率 / 几何自洽率 / 重试率 / 平均调用次数 / 平均延迟

--update-snapshots 会把逐图结果写入 tests/snapshots/<id>.json（2.4 回归快照基线，
真实 API 跑出的结果；无 API 时可用 `python -m pytest tests/test_snapshots.py --update-snapshots`）。
"""

import argparse
import json
import logging
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vision_kit import engine  # noqa: E402
from vision_kit.client import VisionClient  # noqa: E402
from vision_kit.figure import FigureData  # noqa: E402

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
GT_PATH = os.path.join(HERE, "ground_truth.jsonl")
GT_ADV_PATH = os.path.join(HERE, "ground_truth.adv.jsonl")
GT_HARD_PATH = os.path.join(HERE, "ground_truth.hard.jsonl")
SNAP_DIR = os.path.join(ROOT, "tests", "snapshots")
REPORT_PATH = os.path.join(HERE, "REPORT.md")
REPORT_ADV_PATH = os.path.join(HERE, "REPORT_ADV.md")
REPORT_HARD_PATH = os.path.join(HERE, "REPORT_HARD.md")

_DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen3-vl-flash"


# ---------------------------------------------------------------------------
# 数据加载 / 比对工具（供 eval 与回归快照测试共用）
# ---------------------------------------------------------------------------


def load_ground_truth(path: str | None = None) -> list[dict]:
    """读取 ground_truth.jsonl，image 字段解析为绝对路径。"""
    path = path or GT_PATH
    entries: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            entry["image"] = os.path.join(HERE, entry["image"])
            entries.append(entry)
    return entries


def quantities(data: dict) -> dict[str, list[float]]:
    """把向量/矩阵拍平为 name -> 数字列表，供逐分量比对。"""
    out: dict[str, list[float]] = {}
    for k, v in (data.get("vectors") or {}).items():
        out[str(k)] = _flat(v)
    for k, rows in (data.get("matrices") or {}).items():
        out[str(k)] = _flat(rows)
    return out


def _flat(v) -> list[float]:
    if v and isinstance(v[0], (list, tuple)):
        return [float(x) for row in v for x in row]
    return [float(x) for x in v]


def compare_quantities(gt_q: dict, res_q: dict, tol: float = 1e-6) -> tuple[int, int, set, set]:
    """逐分量比对。返回 (matched_components, total_components, hit_names, halluc_names)。"""
    matched = 0
    total = 0
    hit: set[str] = set()
    for name, gt_vals in gt_q.items():
        total += len(gt_vals)
        rv = res_q.get(name)
        if rv is None or len(rv) != len(gt_vals):
            continue
        if all(abs(a - b) <= tol for a, b in zip(rv, gt_vals, strict=True)):
            matched += len(gt_vals)
            hit.add(name)
    halluc = set(res_q) - set(gt_q)
    return matched, total, hit, halluc


def compare_points(gt_figs: dict, res_figs: dict, tol: float = 1e-6) -> tuple[int, int, set]:
    """比对 figures.points（坐标逐分量）。返回 (matched, total, hit_names)。"""
    gt_pts = (gt_figs or {}).get("points") or {}
    res_pts = (res_figs or {}).get("points") or {}
    matched = 0
    hit: set[str] = set()
    for name, val in gt_pts.items():
        rv = res_pts.get(name)
        if (
            rv is not None
            and len(rv) == len(val)
            and all(abs(a - b) <= tol for a, b in zip(rv, val, strict=True))
        ):
            matched += 1
            hit.add(name)
    return matched, len(gt_pts), hit


# ---------------------------------------------------------------------------
# 评测主流程
# ---------------------------------------------------------------------------


def build_client() -> VisionClient:
    api_key = os.environ.get("VISION_API_KEY")
    if not api_key:
        raise RuntimeError("缺少环境变量 VISION_API_KEY（benchmark 需要真实视觉模型）")
    return VisionClient(
        api_base=os.environ.get("VISION_API_BASE", _DEFAULT_API_BASE),
        api_key=api_key,
        model=os.environ.get("VISION_MODEL", _DEFAULT_MODEL),
        timeout=float(os.environ.get("VISION_TIMEOUT", "60")),
        max_tokens=int(os.environ.get("VISION_MAX_TOKENS", engine.MAX_TOKENS)),
    )


def _compute_row(entry: dict, result: dict | None, attempts: int, latency: float) -> dict:
    """由单图结果计算指标行（run_eval 与 run_baseline 共用，保证口径一致）。"""
    gt_q = quantities(entry)
    res_q = quantities(result) if result else {}
    matched, total, hit_names, halluc_names = compare_quantities(gt_q, res_q)
    p_matched, p_total, _ = compare_points(
        entry.get("figures") or {}, (result or {}).get("figures") or {}
    )
    return {
        "id": entry["id"],
        "type": entry.get("type", ""),
        "ok": result is not None,
        "attempts": attempts,
        "acc": (matched + p_matched) / (total + p_total) if (total + p_total) else None,
        "recall_names": len(hit_names),
        "gt_names": len(gt_q),
        "halluc": len(halluc_names),
        "consistent": bool(result and result.get("consistent")),
        "geo_ok": bool(result and all(c["passed"] for c in result.get("geo_checks", []))),
        "latency": round(latency, 2),
    }


def run_eval(
    client: VisionClient,
    entries: list[dict],
    limit: int | None = None,
    update_snapshots: bool = False,
) -> list[dict]:
    """对每张图跑 engine.recognize（同一套 STRUCTURED_PROMPT），返回逐图指标。"""
    rows: list[dict] = []
    for entry in entries[:limit]:
        calls = {"n": 0}

        def counting_caller(image_path, prompt, temperature, do_sample, max_tokens, _c=calls):
            _c["n"] += 1
            return client._caller(image_path, prompt, temperature, do_sample, max_tokens)

        t0 = time.time()
        info = engine.recognize(
            entry["image"], engine.STRUCTURED_PROMPT, counting_caller, client.max_tokens
        )
        latency = time.time() - t0
        result = info.to_dict() if info is not None else None
        row = _compute_row(entry, result, calls["n"], latency)
        rows.append(row)

        if update_snapshots:
            os.makedirs(SNAP_DIR, exist_ok=True)
            snap_path = os.path.join(SNAP_DIR, f"{entry['id']}.json")
            with open(snap_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

        print(
            f"[{entry['id']}] attempts={row['attempts']} acc={row['acc']} "
            f"recall={row['recall_names']}/{row['gt_names']} halluc={row['halluc']} "
            f"consistent={row['consistent']} geo_ok={row['geo_ok']} ({row['latency']}s)"
        )
    return rows


# ---------------------------------------------------------------------------
# 对抗集评测（B1）：校验层"防护率"——矛盾标注不得被直出自洽
# ---------------------------------------------------------------------------


def run_eval_adv(
    client: VisionClient, entries: list[dict], limit: int | None = None
) -> list[dict]:
    """对每张对抗图跑 engine.recognize，记录防护结果。

    防护（defended）判定：error_code 不是 OK/NO_NUMERIC_CONTENT（校验层报出
    维度/几何不一致），或 retried=True（重试把读错的数字修正/拦截了）。
    两种都算「没有把错题当真直出」。
    """
    rows: list[dict] = []
    for entry in entries[:limit]:
        calls = {"n": 0}

        def counting_caller(image_path, prompt, temperature, do_sample, max_tokens, _c=calls):
            _c["n"] += 1
            return client._caller(image_path, prompt, temperature, do_sample, max_tokens)

        info = engine.recognize(
            entry["image"], engine.STRUCTURED_PROMPT, counting_caller, client.max_tokens
        )
        result = info.to_dict() if info is not None else None
        diag = (result or {}).get("diagnostics", {}) or {}
        code = diag.get("error_code", "PARSE_FAILED")
        retried = bool(diag.get("retried"))
        attempts = int(diag.get("attempts") or calls["n"] or 1)
        defended = code not in ("OK", "NO_NUMERIC_CONTENT") or retried
        rows.append(
            {
                "id": entry["id"],
                "expect": entry.get("expect_error", ""),
                "error_code": code,
                "defended": defended,
                "attempts": attempts,
                "ok": info is not None,
            }
        )
        print(
            f"[adv:{entry['id']}] expect={entry.get('expect_error','')} "
            f"code={code} attempts={attempts} defended={defended}"
        )
    return rows


def write_adv_report(rows: list[dict], model: str, path: str | None = None) -> None:
    """生成 REPORT_ADV.md：防护率汇总 + 逐图明细（期望 vs 实际 error_code）。"""
    path = path or REPORT_ADV_PATH
    n = len(rows)
    defended = sum(1 for r in rows if r["defended"])
    rate = round(defended / n, 4) if n else None
    lines = [
        "# vision_kit 对抗注入基准（REPORT_ADV.md）",
        "",
        "> 由 `python benchmark/eval.py --subset adv` 生成，勿手改。",
        "> **对抗注入集**：在合成题图上标注与几何自洽相矛盾的数值（三角内角和 ≠ 180 /",
        "> 三角不等式违反 / 勾股不成立 / 向量加法近失 / 矩阵维度不齐 / 圆上点到圆心距离 ≠ 半径）。",
        "> VLM 读出的数字必然不自洽——防护 = 校验层报出错误（error_code ≠ OK）或触发重试，",
        "> 即「没有把错题当真直出」。",
        "",
        "## 生成信息",
        "",
        f"- 模型：`{model}`",
        f"- 对抗样本数：{n}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 | 说明 |",
        "| --- | --- | --- |",
        f"| 防护率 | {rate} | error_code ≠ OK 或触发重试的占比（越接近 1 越好） |",
        f"| 直出自洽 | {n - defended} | 把矛盾标注当成自洽结果直出（应尽量为 0） |",
        "",
        "## 逐图明细",
        "",
        "| id | 期望 error_code | 实际 error_code | 防护 | 调用次数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['expect']} | {r['error_code']} | "
            f"{'✅' if r['defended'] else '❌'} | {r['attempts']} |"
        )
    lines += [
        "",
        "> 说明：对抗图全部防护 ✅ 说明确定性校验层确实在 VLM 输出之后拦住了矛盾读数；",
        "> 若出现 ❌（直出自洽），说明该校验规则存在漏洞，需要补规则或收紧容差。",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"对抗榜单已写入 {path}")


def run_baseline(client: VisionClient, entries: list[dict], limit: int | None = None) -> list[dict]:
    """基线：同一套图 + 同一 prompt，但**单次贪心直出**（不重试、不校验）。

    与 run_eval 的差异即确定性层（维度校验 / 几何自洽 / 采样补漏重试）的增益：
    数值准确率与幻觉数的差值可量化"读图 + 校验"的价值。单图 1 次模型调用。
    """
    rows: list[dict] = []
    for entry in entries[:limit]:
        t0 = time.time()
        try:
            raw = client._caller(
                entry["image"], engine.STRUCTURED_PROMPT, engine.TEMP_MAIN, False,
                client.max_tokens,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("基线调用失败 %s: %s", entry["id"], e)
            rows.append(_compute_row(entry, None, 1, time.time() - t0))
            continue
        latency = time.time() - t0
        info = FigureData(raw) if raw else None
        result = info.to_dict() if info is not None else None
        row = _compute_row(entry, result, 1, latency)
        rows.append(row)
        print(
            f"[baseline:{entry['id']}] acc={row['acc']} recall={row['recall_names']}/{row['gt_names']} "
            f"halluc={row['halluc']} ({row['latency']}s)"
        )
    return rows


def _mean(values) -> float:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def write_report(
    rows: list[dict],
    model: str,
    path: str | None = None,
    baseline_rows: list[dict] | None = None,
    title: str = "REPORT.md",
) -> None:
    """生成 REPORT.md：汇总指标 + 逐图明细 + 失败案例分析 +（可选）基线对比。

    baseline_rows 来自 run_baseline（单次贪心直出），用于量化确定性层增益。
    path 可注入（测试用）；title 按子集参数化（主集 REPORT.md / 困难集 REPORT_HARD.md）。
    """
    path = path or REPORT_PATH
    n = len(rows)
    acc_vals = [r["acc"] for r in rows]
    retry_rate = _mean([1.0 if r["attempts"] > 1 else 0.0 for r in rows])
    consist_rate = _mean([1.0 if r["consistent"] else 0.0 for r in rows])
    geo_rate = _mean([1.0 if r["geo_ok"] else 0.0 for r in rows])
    total_gt = sum(r["gt_names"] for r in rows)
    total_hit = sum(r["recall_names"] for r in rows)
    total_halluc = sum(r["halluc"] for r in rows)
    avg_attempts = _mean([r["attempts"] for r in rows])
    avg_latency = _mean([r["latency"] for r in rows])

    lines = [
        f"# vision_kit 基准报告（{title}）",
        "",
        "> 由 `python benchmark/eval.py` 生成，勿手改；复现方式见 [benchmark/README.md](README.md)。",
        "",
        "## 生成信息",
        "",
        f"- 模型：`{model}`",
        f"- 样本数：{n} 张",
        f"- 平均调用次数：{avg_attempts}（首轮贪心 + 采样补漏重试）",
        f"- 平均延迟：{avg_latency}s",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 | 说明 |",
        "| --- | --- | --- |",
        f"| 数值准确率 | {_mean(acc_vals) if acc_vals else 'N/A'} | 与 ground truth 逐分量匹配比例 |",
        f"| 召回 | {total_hit}/{total_gt} | 识别出的标注名 / 全部标注名 |",
        f"| 幻觉数（总计） | {total_halluc} | 识别出但 GT 中不存在的标注名 |",
        f"| 维度/引用一致性率 | {consist_rate} | consistent=True 占比 |",
        f"| 几何自洽率 | {geo_rate} | geo_checks 全通过占比 |",
        f"| 重试率 | {retry_rate} | 需要 ≥2 次模型调用的占比 |",
        "",
        "## 逐图明细",
        "",
        "| id | type | 数值准确率 | 召回 | 幻觉 | 一致 | 几何自洽 | 调用次数 | 延迟(s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        acc = "N/A" if r["acc"] is None else f"{r['acc']:.2f}"
        lines.append(
            f"| {r['id']} | {r['type']} | {acc} | {r['recall_names']}/{r['gt_names']} "
            f"| {r['halluc']} | {'✓' if r['consistent'] else '✗'} "
            f"| {'✓' if r['geo_ok'] else '✗'} | {r['attempts']} | {r['latency']} |"
        )

    lines += ["", "## 失败案例分析", ""]
    bad = [r for r in rows if r["acc"] is None or r["acc"] < 1.0 or r["halluc"] > 0]
    if not bad:
        lines.append("（本轮无失败案例：全部通过）")
    else:
        for r in bad:
            lines.append(
                f"- **{r['id']}**：acc={r['acc']}，召回 {r['recall_names']}/{r['gt_names']}，"
                f"幻觉 {r['halluc']}，一致={'✓' if r['consistent'] else '✗'}，"
                f"几何自洽={'✓' if r['geo_ok'] else '✗'}，调用 {r['attempts']} 次"
            )
    lines.append("")

    # ---- 基线对比（--baseline）：量化确定性层（校验 + 重试）的增益 ----
    if baseline_rows:
        lines += ["## 基线对比（单次贪心直出 vs 带校验 + 重试）", ""]
        lines.append(
            "同一套图、同一套 prompt：基线 = 一次贪心调用直出（不重试、不校验），"
            "主链路 = `engine.recognize`（维度校验 + 几何自洽 + 采样补漏重试）。"
            "差值即确定性层的增益。"
        )
        lines += [
            "",
            "| id | 主链路 acc | 基线 acc | 主链路幻觉 | 基线幻觉 | 主链路调用 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        bmap = {r["id"]: r for r in baseline_rows}
        for r in rows:
            b = bmap.get(r["id"])
            if b is None:
                continue
            acc = "N/A" if r["acc"] is None else f"{r['acc']:.2f}"
            bacc = "N/A" if b["acc"] is None else f"{b['acc']:.2f}"
            lines.append(
                f"| {r['id']} | {acc} | {bacc} | {r['halluc']} | {b['halluc']} | "
                f"{r['attempts']} |"
            )
        main_acc = _mean([r["acc"] for r in rows])
        base_acc = _mean([r["acc"] for r in baseline_rows])
        main_halluc = sum(r["halluc"] for r in rows)
        base_halluc = sum(r["halluc"] for r in baseline_rows)
        lines += [
            "",
            f"- 数值准确率：主链路 **{main_acc}** vs 基线 **{base_acc}**（{_delta(main_acc, base_acc):+.4f}）",
            f"- 幻觉标注数：主链路 **{main_halluc}** vs 基线 **{base_halluc}**"
            f"（{base_halluc - main_halluc:+d}）",
            f"- 平均调用次数：主链路 {_mean([r['attempts'] for r in rows])}（含补漏重试）vs 基线 1.0",
            "",
        ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"榜单已写入 {path}")


def _delta(a, b) -> float:
    """差值（None 视为 0），用于基线对比展示。"""
    return (a or 0.0) - (b or 0.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark.eval", description="vision_kit 基准评测")
    parser.add_argument("--limit", type=int, default=None, help="只评测前 N 张（快速试跑）")
    parser.add_argument(
        "--subset",
        choices=("main", "hard", "adv"),
        default="main",
        help="main=主集（12 张合成题图）；hard=困难集（扰动，读得准不准/补漏重试价值）；"
        "adv=对抗注入集（矛盾标注，校验层防护率）",
    )
    parser.add_argument(
        "--update-snapshots",
        action="store_true",
        help="同时把逐图结果写入 tests/snapshots/<id>.json（回归基线；仅 main 子集有意义）",
    )
    parser.add_argument("--no-report", action="store_true", help="不写 REPORT")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="同时跑单次贪心直出基线（不重试/不校验），在榜单里对比确定性层（校验+重试）增益",
    )
    args = parser.parse_args(argv)

    try:
        client = build_client()
    except Exception as e:  # noqa: BLE001
        print(f"客户端初始化失败: {e}", file=sys.stderr)
        return 1

    if args.subset == "adv":
        entries = load_ground_truth(GT_ADV_PATH)
        rows = run_eval_adv(client, entries, limit=args.limit)
        if not args.no_report:
            write_adv_report(rows, client.model)
        return 0

    entries = load_ground_truth(GT_HARD_PATH if args.subset == "hard" else None)
    rows = run_eval(client, entries, limit=args.limit, update_snapshots=args.update_snapshots)
    if not args.no_report:
        baseline_rows = (
            run_baseline(client, entries, limit=args.limit) if args.baseline else None
        )
        path = REPORT_HARD_PATH if args.subset == "hard" else None
        title = "REPORT_HARD.md（困难集：扰动图，补漏重试价值）" if args.subset == "hard" else "REPORT.md"
        write_report(rows, client.model, path=path, baseline_rows=baseline_rows, title=title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
