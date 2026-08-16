"""benchmark 基准测试 —— ground truth 完整性 / 自洽性 / 比对工具。

不依赖任何 API：验证基准数据自身满足全部几何校验（引擎读对了应当全部通过），
并覆盖 eval 的比对函数。
"""

import json
import math
import os
import tempfile

from vision_kit.figure import FigureData  # noqa: E402

from benchmark.eval import (  # noqa: E402
    compare_points,
    compare_quantities,
    load_ground_truth,
    quantities,
)

GT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmark", "ground_truth.jsonl"
)
GT_ADV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmark",
    "ground_truth.adv.jsonl",
)
GT_HARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmark",
    "ground_truth.hard.jsonl",
)


def _load_raw() -> list[dict]:
    entries = []
    with open(GT_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def test_ground_truth_well_formed_and_images_exist():
    entries = load_ground_truth()
    assert len(entries) >= 10
    for e in entries:
        assert e["id"] and e["image"] and e["type"]
        assert os.path.exists(e["image"]), f"图片缺失: {e['image']}"
        assert isinstance(e.get("vectors", {}), dict)
        assert isinstance(e.get("matrices", {}), dict)


def test_ground_truth_self_consistent_geometry():
    """GT 自身的几何自洽：三角内角和 ≈ 180、线段长度 ≈ 端点距离、向量加法成立。"""
    entries = _load_raw()
    for e in entries:
        figs = e.get("figures") or {}
        pts = figs.get("points") or {}
        # 线段长度 vs 端点距离（20% 容差，与 geometry.check_segment_lengths 一致）
        for seg in figs.get("segments") or []:
            ep = seg.get("endpoints", [])
            if len(ep) != 2 or ep[0] not in pts or ep[1] not in pts:
                continue
            p0, p1 = pts[ep[0]], pts[ep[1]]
            dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            assert abs(dist - seg["length"]) <= 0.2 * max(1.0, dist, seg["length"]), e["id"]
        # 三角内角和
        angs = [a["value"] for a in figs.get("angles") or []]
        if len(angs) == 3:
            assert abs(sum(angs) - 180.0) <= 1.0, e["id"]
        # 向量加法关系（GT 有 b = a1 + a2 之类的图，分量式 [dx, dy]）
        vecs = e.get("vectors") or {}
        if e["id"] == "vec_para_01":
            assert [vecs["a1"][0] + vecs["a2"][0], vecs["a1"][1] + vecs["a2"][1]] == vecs["b"]
        if e["id"] == "vec_grid_01":
            assert [vecs["a"][0] + vecs["b"][0], vecs["a"][1] + vecs["b"][1]] == vecs["c"]


def test_ground_truth_passes_engine_geometry():
    """把 GT 喂给 FigureData → geo_checks 应全部通过（读对了就该自洽）。"""
    for e in _load_raw():
        raw = json.dumps(e, ensure_ascii=False)
        f = FigureData(raw)
        bad = [c for c in f.geo_checks if not c["passed"]]
        assert not bad, f"{e['id']} 的 GT 竟然几何不自洽: {bad}"


# ---------------------------------------------------------------------------
# eval 比对工具
# ---------------------------------------------------------------------------


def test_quantities_flattens_vectors_and_matrices():
    data = {"vectors": {"a": [0, 0, 2, 1]}, "matrices": {"M": [[1, 2], [3, 4]]}}
    q = quantities(data)
    assert q == {"a": [0.0, 0.0, 2.0, 1.0], "M": [1.0, 2.0, 3.0, 4.0]}


def test_compare_quantities_exact_and_hallucination():
    gt = {"a": [1.0, 2.0], "b": [3.0, 4.0]}
    res = {"a": [1.0, 2.0], "c": [9.0, 9.0]}  # b 漏读 + c 幻觉
    matched, total, hit, halluc = compare_quantities(gt, res)
    assert (matched, total) == (2, 4)
    assert hit == {"a"}
    assert halluc == {"c"}


def test_compare_quantities_tolerance():
    gt = {"a": [1.0, 2.0]}
    res = {"a": [1.0000001, 1.9999999]}
    matched, total, _, _ = compare_quantities(gt, res)
    assert (matched, total) == (2, 2)


def test_compare_points():
    gt_figs = {"points": {"A": [0, 0], "B": [6, 0]}}
    res_figs = {"points": {"A": [0, 0], "B": [6, 1]}}
    matched, total, hit = compare_points(gt_figs, res_figs)
    assert (matched, total) == (1, 2)
    assert hit == {"A"}


# ---------------------------------------------------------------------------
# eval 全流程（离线：假 caller 模拟“读对了”的视觉模型）
# ---------------------------------------------------------------------------


class _PerfectCaller:
    """返回 ground truth 对应 JSON 的假 caller（模拟完美的视觉模型）。"""

    max_tokens = 1024

    def __init__(self, entries: list[dict]):
        self._by_path = {os.path.normpath(e["image"]): e for e in entries}

    def _caller(self, image_path, prompt, temperature, do_sample, max_tokens):
        e = self._by_path.get(os.path.normpath(image_path))
        if not e:
            return "{}"
        payload = {k: e[k] for k in ("type", "vectors", "matrices", "figures", "note") if k in e}
        return json.dumps(payload, ensure_ascii=False)


def test_eval_pipeline_offline_perfect_scores():
    """假 caller 读对一切 → 数值准确率/一致性/几何自洽全满分，单次调用无重试。"""
    from benchmark import eval as ev

    entries = load_ground_truth()
    rows = ev.run_eval(_PerfectCaller(entries), entries[:4])
    assert len(rows) == 4
    for r in rows:
        assert r["acc"] == 1.0, r
        assert r["consistent"] is True
        assert r["geo_ok"] is True
        assert r["attempts"] == 1
        assert r["halluc"] == 0


def test_write_report_renders_metrics():
    """write_report 生成含汇总/明细/失败案例的榜单。"""
    from benchmark import eval as ev

    rows = [
        {"id": "ok_01", "type": "向量图", "ok": True, "attempts": 1, "acc": 1.0,
         "recall_names": 2, "gt_names": 2, "halluc": 0, "consistent": True,
         "geo_ok": True, "latency": 0.4},
        {"id": "bad_01", "type": "几何图", "ok": True, "attempts": 3, "acc": 0.5,
         "recall_names": 1, "gt_names": 2, "halluc": 1, "consistent": False,
         "geo_ok": False, "latency": 2.1},
    ]
    fd, out = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    try:
        ev.write_report(rows, "fake-model", path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "fake-model" in content
        assert "汇总指标" in content and "逐图明细" in content and "失败案例分析" in content
        assert "ok_01" in content and "bad_01" in content
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# B1 对抗注入集：矛盾标注必须被校验层抓出
# ---------------------------------------------------------------------------


def _load_jsonl(path: str) -> list[dict]:
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def test_adversarial_gt_well_formed():
    """对抗集 GT：每条含 expect_error，图片存在。"""
    entries = _load_jsonl(GT_ADV_PATH)
    assert len(entries) >= 5
    for e in entries:
        assert e["expect_error"] in ("GEOMETRY_INCONSISTENT", "DIMENSION_MISMATCH"), e["id"]
        img = os.path.join(os.path.dirname(GT_ADV_PATH), e["image"])
        assert os.path.exists(img), f"对抗图缺失: {img}"


def test_adversarial_samples_caught_by_checks():
    """对抗样本喂给 FigureData → error_code 必须命中 expect_error（校验层抓出矛盾）。"""
    for e in _load_jsonl(GT_ADV_PATH):
        fd = FigureData(json.dumps(e, ensure_ascii=False))
        assert fd.error_code == e["expect_error"], (
            f"{e['id']}: 期望 {e['expect_error']}，实际 {fd.error_code}"
        )


def test_adversarial_offline_eval_all_defended():
    """假 caller 读对对抗 GT（矛盾数值）→ run_eval_adv 全部防护（error_code≠OK 或重试）。"""
    from benchmark import eval as ev

    entries = load_ground_truth(GT_ADV_PATH)

    class _AdvCaller:
        max_tokens = 1024

        def __init__(self, ents):
            self._by_path = {os.path.normpath(e["image"]): e for e in ents}

        def _caller(self, image_path, prompt, temperature, do_sample, max_tokens):
            e = self._by_path.get(os.path.normpath(image_path))
            if not e:
                return "{}"
            payload = {k: e[k] for k in ("type", "vectors", "matrices", "figures", "note") if k in e}
            return json.dumps(payload, ensure_ascii=False)

    rows = ev.run_eval_adv(_AdvCaller(entries), entries)
    assert len(rows) == len(entries)
    for r in rows:
        assert r["defended"] is True, f"{r['id']}: {r}"
        assert r["error_code"] == r["expect"], f"{r['id']}: {r}"


def test_write_adv_report_renders_defense_metrics():
    from benchmark import eval as ev

    rows = [
        {"id": "a1", "expect": "GEOMETRY_INCONSISTENT", "error_code": "GEOMETRY_INCONSISTENT",
         "defended": True, "attempts": 2, "ok": True},
        {"id": "a2", "expect": "DIMENSION_MISMATCH", "error_code": "DIMENSION_MISMATCH",
         "defended": True, "attempts": 1, "ok": True},
    ]
    fd, out = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    try:
        ev.write_adv_report(rows, "fake-model", path=out)
        with open(out, encoding="utf-8") as f:
            content = f.read()
        assert "防护率" in content and "1.0" in content
        assert "a1" in content and "a2" in content
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# B2 困难集：扰动不改内容，读对了仍自洽
# ---------------------------------------------------------------------------


def test_hard_gt_well_formed_and_self_consistent():
    """困难集 GT：图片存在、数值与主集一致（读对了就应全部自洽）。"""
    main = {e["id"]: e for e in _load_jsonl(GT_PATH)}
    for e in _load_jsonl(GT_HARD_PATH):
        img = os.path.join(os.path.dirname(GT_HARD_PATH), e["image"])
        assert os.path.exists(img), f"困难图缺失: {img}"
        base_id = e["id"].removesuffix("_hard01")
        assert base_id in main, e["id"]
        # 数值与主集一致（扰动不改内容）
        assert e.get("vectors") == main[base_id].get("vectors"), e["id"]
        assert e.get("matrices") == main[base_id].get("matrices"), e["id"]
        # 读对了就该自洽
        fd = FigureData(json.dumps(e, ensure_ascii=False))
        bad = [c for c in fd.geo_checks if not c["passed"]]
        assert not bad, f"{e['id']} 竟然几何不自洽: {bad}"


def test_perturb_is_deterministic():
    """同一 seed 的扰动输出逐像素一致（确定性基准）。"""
    from PIL import Image

    from benchmark.generate_images import _perturb

    base = Image.new("RGB", (120, 80), "white")
    a = _perturb(base, seed=42)
    b = _perturb(base, seed=42)
    assert a.tobytes() == b.tobytes()
    c = _perturb(base, seed=43)
    # 不同 seed 通常产生不同扰动（不绝对，但本例的三种扰动类型不同种子几乎必不同）
    assert list(a.size) != list(c.size) or a.tobytes() != c.tobytes()
