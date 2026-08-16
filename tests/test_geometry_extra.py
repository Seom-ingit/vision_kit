"""geometry 新增几何自洽校验规则（三角形不等式 / 勾股定理）的单元测试。

覆盖：
- check_triangle_inequality：三边构成三角形时最长边 < 另两边之和（可判负）；
- check_pythagoras：声明直角 → 校验三边勾股关系（可判负）；
  无直角声明但三边满足勾股 → 仅正面确认；三边不全 → 不判定；
- GT 回归护栏：把 benchmark/ground_truth.jsonl 每行喂给 FigureData，geo_checks 应全通过。
"""

import json
import os

from vision_kit.figure import FigureData  # noqa: E402
from vision_kit.geometry import (  # noqa: E402
    check_pythagoras,
    check_triangle_inequality,
    overall_consistent,
    run_geometry_checks,
)

GT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmark", "ground_truth.jsonl"
)


def _geo_data(seg_lengths, angles=None):
    """构造 geometry.run_geometry_checks 所需的图元数据。

    刻意不提供 points：让既有的 check_segment_lengths（坐标 vs 标注长度）跳过，
    使断言聚焦于新增的 triangle_inequality / pythagoras 规则本身。
    """
    segments = [{"endpoints": [a, b], "length": length} for a, b, length in seg_lengths]
    figures = {"angles": angles or []}
    return {"points": {}, "segments": segments, "figures": figures}


# ---------------------------------------------------------------------------
# 三角形不等式
# ---------------------------------------------------------------------------


def test_triangle_inequality_ok():
    data = _geo_data([("A", "B", 3.0), ("A", "C", 4.0), ("B", "C", 5.0)])
    checks = run_geometry_checks(data)
    ti = [c for c in checks if c["rule"] == "triangle_inequality"]
    assert len(ti) == 1 and ti[0]["passed"], checks
    assert overall_consistent(checks)


def test_triangle_inequality_violated():
    # 9 ≥ 3+4 → 最长边 ≥ 另两边之和，判负
    data = _geo_data([("A", "B", 3.0), ("A", "C", 4.0), ("B", "C", 9.0)])
    checks = run_geometry_checks(data)
    bad = [c for c in checks if c["rule"] == "triangle_inequality" and not c["passed"]]
    assert bad, checks
    assert not overall_consistent(checks)


def test_triangle_inequality_insufficient():
    # 只有 2 条线段 → 两条规则都不判定（返回 []）
    data = _geo_data([("A", "B", 3.0), ("A", "C", 4.0)])
    assert check_triangle_inequality(data["points"], data["segments"]) == []
    assert check_pythagoras(data["points"], data["segments"], data["figures"]["angles"]) == []


# ---------------------------------------------------------------------------
# 勾股定理
# ---------------------------------------------------------------------------


def test_pythagoras_right_triangle_ok():
    # 条件 A：A 为直角，3²+4²=5² → 通过
    angles = [{"vertex": "A", "sides": ["AB", "AC"], "value": 90.0}]
    data = _geo_data([("A", "B", 4.0), ("A", "C", 3.0), ("B", "C", 5.0)], angles=angles)
    checks = run_geometry_checks(data)
    py = [c for c in checks if c["rule"] == "pythagoras"]
    assert py and all(c["passed"] for c in py), checks
    assert overall_consistent(checks)


def test_pythagoras_hyp_wrong():
    # 条件 A：直角但斜边标错。注意 20% 相对容差下 6 仍通过（|6-5|=1 ≤ 0.2·6=1.2），
    # 故用 7：|7-5|=2 > 0.2·7=1.4 → 判负（同时 7 < 3+4 保持三角形不等式成立）。
    angles = [{"vertex": "A", "sides": ["AB", "AC"], "value": 90.0}]
    data = _geo_data([("A", "B", 3.0), ("A", "C", 4.0), ("B", "C", 7.0)], angles=angles)
    checks = run_geometry_checks(data)
    bad = [c for c in checks if c["rule"] == "pythagoras" and not c["passed"]]
    assert bad, checks
    assert not overall_consistent(checks)


def test_pythagoras_positive_only():
    # 条件 B：无 90° 角声明但 5-12-13 满足勾股 → 正面确认（passed=True）
    points = {"A": [0, 0], "B": [5, 0], "C": [0, 12]}
    segments = [
        {"endpoints": ["A", "B"], "length": 5.0},
        {"endpoints": ["A", "C"], "length": 12.0},
        {"endpoints": ["B", "C"], "length": 13.0},
    ]
    assert check_pythagoras(points, segments, []) == [
        {
            "rule": "pythagoras",
            "passed": True,
            "detail": "三边 5/12/13 满足勾股关系（5²+12²=13²），可能是直角三角形",
        }
    ]


def test_pythagoras_ordinary_triangle_no_conclusion():
    # 条件 B：3-4-6 不满足勾股 → 无结论（返回 []，普通三角形不违反任何规则）
    data = _geo_data([("A", "B", 3.0), ("A", "C", 4.0), ("B", "C", 6.0)])
    assert check_pythagoras(data["points"], data["segments"], []) == []


def test_pythagoras_incomplete_skips():
    # 条件 C：声明了 90° 但缺一条边（数据不全）→ 跳过不判定（返回 []）
    points = {"A": [0, 0], "B": [4, 0], "C": [0, 3]}
    segments = [{"endpoints": ["A", "B"], "length": 4.0}, {"endpoints": ["A", "C"], "length": 3.0}]
    angles = [{"vertex": "A", "sides": ["AB", "AC"], "value": 90.0}]
    assert check_pythagoras(points, segments, angles) == []


# ---------------------------------------------------------------------------
# GT 回归护栏：读对了就该自洽
# ---------------------------------------------------------------------------


def test_ground_truth_passes_new_geometry_rules():
    """把 benchmark/ground_truth.jsonl 每一行喂给 FigureData → geo_checks 应全部通过。"""
    with open(GT_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            fd = FigureData(json.dumps(entry, ensure_ascii=False))
            bad = [c for c in fd.geo_checks if not c["passed"]]
            assert not bad, f"{entry['id']} 的 GT 竟然几何不自洽: {bad}"
