"""2.4 回归快照：改提示词/校验规则后与历史快照做数值级比对（不是字符串比对）。

需要真实视觉模型（环境变量 VISION_API_KEY），离线时自动跳过：
    pytest tests/test_snapshots.py                      # 比对现有快照
    pytest tests/test_snapshots.py --update-snapshots   # 人工确认改进后重录基线

快照基线由 benchmark/eval.py --update-snapshots 或本测试 --update-snapshots 写入
tests/snapshots/<id>.json；比对按向量/矩阵逐分量容差 1e-6，points 坐标同样逐分量比对。
"""

import json
import os

import pytest

from benchmark.eval import compare_points, compare_quantities, load_ground_truth  # noqa: E402

SNAP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "snapshots"
)

pytestmark = pytest.mark.snapshot


def _snap_path(iid: str) -> str:
    return os.path.join(SNAP_DIR, f"{iid}.json")


def test_snapshots_numeric_match(request):
    """真实 API 抽取结果与快照逐分量一致（容差 1e-6）；缺快照或 --update-snapshots 时重录。"""
    if not os.environ.get("VISION_API_KEY"):
        pytest.skip("需要 VISION_API_KEY 才能跑真实 API 快照（离线只跑解析/渲染单测）")

    from vision_kit import engine  # noqa: PLC0415
    from vision_kit.client import VisionClient  # noqa: PLC0415

    client = VisionClient(
        api_base=os.environ.get(
            "VISION_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        api_key=os.environ["VISION_API_KEY"],
        model=os.environ.get("VISION_MODEL", "qwen3-vl-flash"),
        timeout=float(os.environ.get("VISION_TIMEOUT", "60")),
        max_tokens=int(os.environ.get("VISION_MAX_TOKENS", engine.MAX_TOKENS)),
    )
    update = request.config.getoption("--update-snapshots")
    failures: list[str] = []

    for entry in load_ground_truth():
        info = engine.recognize(
            entry["image"], engine.STRUCTURED_PROMPT, client._caller, client.max_tokens
        )
        result = info.to_dict() if info is not None else None
        path = _snap_path(entry["id"])
        if update:
            os.makedirs(SNAP_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            continue
        if not os.path.exists(path):
            failures.append(
                f"{entry['id']}: 缺快照（先跑 pytest --update-snapshots 或 benchmark/eval.py --update-snapshots 生成）"
            )
            continue
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        # 回归比对：当前结果 vs 快照（数值级，逐分量容差 1e-6）
        res_q = _gt_q(result or {})
        snap_q = _gt_q(snap or {})
        matched, total, hit, halluc = compare_quantities(snap_q, res_q)
        p_matched, p_total, _ = compare_points(
            (snap or {}).get("figures") or {}, (result or {}).get("figures") or {}
        )
        if total and matched < total:
            failures.append(f"{entry['id']}: 数值失配 {matched}/{total}（快照 vs 当前结果）")
        if p_total and p_matched < p_total:
            failures.append(f"{entry['id']}: 点坐标失配 {p_matched}/{p_total}（快照 vs 当前结果）")
        if halluc:
            failures.append(f"{entry['id']}: 新增标注（快照中不存在）{sorted(halluc)}")

    assert not failures, "回归快照失败：\n" + "\n".join(failures)


def _gt_q(data: dict) -> dict:
    """取向量/矩阵（不含 figures，points 单独比对）。"""
    from benchmark.eval import quantities  # noqa: PLC0415

    return quantities(data)
