"""几何一致性校验：在"维度一致"之上，用确定性规则检验抽取结果是否几何/代数自洽。

纯逻辑模块，不依赖模型，也不依赖任何宿主导入；风格与 figure.py 一致（表驱动 + 纯函数）。
被 FigureData 引用，用于把"读出的数字"变成"可信的证据"——能自动发现 VLM 幻觉
（角度和不等于 180、向量加法不匹配、矩阵乘法维度不符、值域非法、
三角形不等式被违反、标注直角与三边长矛盾等）。

每个来源的图数据由调用方归一化后传入，本模块只做几何/代数推理：
    run_geometry_checks(data: dict) -> list[dict]

返回的每条 check：
    {"rule": str, "passed": bool, "detail": str}
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    """浮点近似相等，容忍 -0.0 / 0.0 之类的表示差异。"""
    return abs(a - b) <= tol


def _vec(v: Any) -> list[float] | None:
    """规范化一个值为向量；非数字或空返回 None。"""
    if not isinstance(v, (list, tuple)):
        return None
    out: list[float] = []
    for x in v:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            return None
    return out


def _flatten_number(value: Any) -> float | None:
    """尽可能把值规约成一个标量：单元素向量/矩阵收敛到标量，否则 None。"""
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return _flatten_number(value[0])
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 校验规则（把数据归一化为 vectors / matrices / scalars）
# ---------------------------------------------------------------------------


def _with_vectors(data: dict) -> dict:
    """补充两种派生视角：
    - vectors: 所有可当列向量理解的一维标注
    - scalars : 名字形如 a/b/l/AB/∠(/θ 且能收敛为单标量的标注
    """
    vectors: dict[str, list[float]] = {}
    scalars: dict[str, float] = {}

    for name, value in data.get("vectors", {}).items():
        v = _vec(value)
        if not v:
            continue
        # 长度为 1 的向量实质是标量（角度/长度/单值），降级为标量参与标量校验
        if len(v) == 1:
            scalars[name] = v[0]
            continue
        s = _flatten_number(value)
        if s is not None and _looks_like_scalar_name(name):
            scalars[name] = s
        vectors[name] = v

    for name, value in data.get("matrices", {}).items():
        # 1x1 或 1xN 矩阵常可等价为标量/列向量
        if isinstance(value, (list, tuple)) and len(value) == 1:
            row = value[0]
            if isinstance(row, (list, tuple)) and len(row) == 1:
                scalars[name] = float(row[0])
                vectors[name] = [float(row[0])]
            elif isinstance(row, (list, tuple)):
                v = _vec(row)
                if v:
                    vectors[name] = v

    # 1.1 图元 DOM：角的度数、圆的半径作为标量参与角度和/值域校验
    figures = data.get("figures") or {}
    for ang in figures.get("angles") or []:
        name = ang.get("vertex", "")
        val = ang.get("value")
        if name and isinstance(val, (int, float)) and not isinstance(val, bool):
            scalars[f"∠{name}"] = float(val)
    for circ in figures.get("circles") or []:
        name = circ.get("center", "")
        val = circ.get("radius")
        if name and isinstance(val, (int, float)) and not isinstance(val, bool):
            scalars[f"r_{name}"] = float(val)
    # 1.2 线段标注长度也纳入标量池（值域非负检查；P3-2：此前负长度
    #     只能靠 check_segment_lengths 间接抓，无坐标时直接漏过）。
    #     segments 规范上在顶层（与 check_segment_lengths 一致），figures 内兜底。
    for seg in figures.get("segments") or data.get("segments") or []:
        ep = seg.get("endpoints") or []
        val = seg.get("length")
        if len(ep) == 2 and isinstance(val, (int, float)) and not isinstance(val, bool):
            scalars[f"|{ep[0]}{ep[1]}|"] = float(val)

    return {"vectors": vectors, "scalars": scalars}


def _looks_like_scalar_name(name: str) -> bool:
    """判断标注名更像"标量"还是"向量"。
    启发式：单个小写字母 / x,y,z,r / 长度形 AB、角度形 ∠A、θ 等视为标量。
    """
    n = name.strip()
    if len(n) == 1 and n.islower():
        return True
    return any(n.startswith(p) for p in ("∠", "角度", "θ", "α", "β", "γ", "°"))


# ---------------------------------------------------------------------------
# 三角内角和
# ---------------------------------------------------------------------------


def check_triangle_sum(scalars: dict[str, float]) -> dict | None:
    """若图中恰好有三个角度标量，检验其和是否为 180°。
    返回 check 或 None（数据不足，不判定）。
    """
    angles = {name: val for name, val in scalars.items() if _looks_like_angle(name, val)}
    if len(angles) != 3:
        return None
    vals = list(angles.values())
    total = sum(vals)
    ok = _approx(total, 180.0, tol=1.0)
    detail = f"三角形内角 {'+'.join(f'{n}={v:g}' for n, v in angles.items())} = {total:g}"
    if not ok:
        detail += f"，预期 180，误差 {total - 180:+.1f}°"
    return {"rule": "triangle_sum", "passed": ok, "detail": detail}


def _looks_like_angle(name: str, value: float) -> bool:
    """角度判定：名字带角标识，数值在合理角度域 (0, 180)。"""
    n = name.strip()
    if (
        n.startswith("∠")
        or "角" in n
        or n.lower().startswith("theta")
        or n.lower().startswith("angle")
        or n in ("α", "β", "γ")
    ):
        return 0.0 < value < 180.0
    # 三角形顶点常用单个大写字母标注（A/B/C）；仅当作为唯一标量语境由调用方 gating len==3 判定
    if len(n) == 1 and n.isalpha() and n.isupper():
        return 0.0 < value < 180.0
    return False


# ---------------------------------------------------------------------------
# 向量加法关系：a1 + a2 = b
# ---------------------------------------------------------------------------


def check_vector_sums(vectors: dict[str, list[float]]) -> list[dict]:
    """两两向量做加法，看结果是否命中另一个已知向量。

    语义上只断言"声明的关系"：没有声明时，某个组合的和不等于任何向量并不矛盾。
    因此本规则只输出两类结论而不产生大批误报：
      - passed=True  ：sum 精确命中某向量（如 a1+a2=b）——正面确认；
      - passed=False ：sum 与某向量"接近但不等"（near-miss，真实幻觉特征：本应成立的
        关系因读数微差而失败）。
    和与任何向量都相差很远（未断言的关系）→ 不产生结论，避免把无关组合当成错误。
    """
    out: list[dict] = []
    items = list(vectors.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            n1, v1 = items[i]
            n2, v2 = items[j]
            if len(v1) != len(v2):
                continue
            s = [a + b for a, b in zip(v1, v2, strict=True)]
            for n3, v3 in items:
                if n3 in (n1, n2):
                    continue
                if len(v3) != len(s):
                    continue
                if all(_approx(a, b) for a, b in zip(s, v3, strict=True)):
                    out.append(
                        {
                            "rule": "vector_sum",
                            "passed": True,
                            "detail": f"{n1}({_fmt(v1)}) + {n2}({_fmt(v2)}) = {n3}({_fmt(v3)}) OK",
                        }
                    )
                elif _near(s, v3):
                    out.append(
                        {
                            "rule": "vector_sum",
                            "passed": False,
                            "detail": f"{n1} + {n2} 本应 = {n3}({_fmt(v3)})，但和 {_fmt(s)} 接近不等",
                        }
                    )
    return out


def _near(a: list[float], b: list[float], frac: float = 0.15) -> bool:
    """判断两个等长向量是否"接近但不等"（多数分量误差小，作为幻觉近失信号）。
    用于只在"本该成立却差一点"时判负，避免无关组合被误报为错误。
    """
    if len(a) != len(b):
        return False
    matches = 0
    for x, y in zip(a, b, strict=True):
        scale = max(1.0, abs(x), abs(y))
        if abs(x - y) <= frac * scale:
            matches += 1
    # 接近须占"绝大多数"：除至多一个分量外其余都接近（len-1）。
    # 向量加法是逐分量精确关系，真实近失通常只有一个分量读错明显；
    # 此前的 max(1, len//2) 对 3 维只需 1/3 分量碰巧接近就误判
    # （如 [5,1,4] 与 [1,1,2] 仅中间分量恰好相同 → 假"近失"）。
    return matches >= len(a) - 1 and any(x != y for x, y in zip(a, b, strict=True))


# ---------------------------------------------------------------------------
# 矩阵乘法维度
# ---------------------------------------------------------------------------


def check_matrix_multiplicity(matrices: dict[str, list[list[float]]]) -> list[dict]:
    """两矩阵做乘法（形状允许时），结果是否命中某已知矩阵。

    维度不匹配不是"矛盾"——矩阵乘法本身不可交换，B·A 无法相乘不代表读数错误。
    因此本规则只输出**正面确认**（某两个矩阵可乘 → passed:True），不产生 failed，
    避免把"互不可乘的矩阵对"当成幻觉。
    """
    out: list[dict] = []
    shapes: dict[str, tuple[int, int]] = {
        name: (len(rows), len(rows[0])) if rows else (0, 0)
        for name, rows in matrices.items()
        if rows
    }
    for n1, s1 in shapes.items():
        for n2, s2 in shapes.items():
            if n1 == n2:
                continue
            if s1[1] == s2[0]:
                out.append(
                    {
                        "rule": "matrix_multiplicity",
                        "passed": True,
                        "detail": f"({n1}:{s1[0]}×{s1[1]}) · ({n2}:{s2[0]}×{s2[1]}) 可乘 → {s1[0]}×{s2[1]} OK",
                    }
                )
    return out


# ---------------------------------------------------------------------------
# 值域 / 非负检查
# ---------------------------------------------------------------------------


def check_plausible_ranges(
    vectors: dict[str, list[float]], scalars: dict[str, float]
) -> list[dict]:
    """值域合理性：向量分量、长度类标量非负；角度在 (0,180)。
    可抓"负数长度"之类明显幻觉。
    """
    out: list[dict] = []
    for name, v in vectors.items():
        # 向量分量可含负数坐标（如点 (-2,0)），只有"长度/距离"类标注才判负值
        lengthish = (
            len(name.strip()) >= 2
            and not _looks_like_angle(name, 0)
            and any(k in name for k in ("len", "AB", "a", "b", "c", "l", "length"))
        )
        for i, x in enumerate(v):
            if x != x:  # NaN
                out.append(
                    {
                        "rule": "plausible_range",
                        "passed": False,
                        "detail": f"{name}[{i}] 为非数字（NaN）",
                    }
                )
                continue
            if lengthish and x < 0:
                out.append(
                    {
                        "rule": "plausible_range",
                        "passed": False,
                        "detail": f"{name}[{i}] = {x:g} 为负值（长度可疑）",
                    }
                )
    for name, val in scalars.items():
        if val != val:  # NaN
            out.append(
                {"rule": "plausible_range", "passed": False, "detail": f"{name} 为非数字（NaN）"}
            )
            continue
        if _looks_like_angle(name, val):
            if not (0.0 < val < 180.0):
                out.append(
                    {
                        "rule": "plausible_range",
                        "passed": False,
                        "detail": f"角度 {name} = {val:g} 超出 (0,180)",
                    }
                )
        elif val < 0:
            out.append(
                {"rule": "plausible_range", "passed": False, "detail": f"{name} = {val:g} 为负值"}
            )
    return out


# ---------------------------------------------------------------------------
# 线段长度 vs 端点坐标距离（1.1 图元 DOM）
# ---------------------------------------------------------------------------


def check_segment_lengths(points: dict[str, list[float]], segments: list[dict]) -> list[dict]:
    """线段"标注长度"与端点坐标距离的自洽（图中同时给出两者时）。

    坐标与长度可能用不同单位/刻度，故用 20% 相对容差，只抓明显不一致
    （VLM 把长度 8 读成 4 之类）——小偏差不误报。
    """
    out: list[dict] = []
    for seg in segments:
        ep = seg.get("endpoints") or []
        if len(ep) != 2 or ep[0] not in points or ep[1] not in points:
            continue
        p0, p1 = points[ep[0]], points[ep[1]]
        if len(p0) < 2 or len(p1) < 2:
            continue
        dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        length = seg.get("length")
        if length is None:
            continue
        try:
            length = float(length)
        except (TypeError, ValueError):
            continue
        scale = max(1.0, dist, length)
        ok = abs(dist - length) <= 0.2 * scale
        detail = f"|{ep[0]}{ep[1]}| 坐标距离 {dist:g} vs 标注长度 {length:g}"
        if not ok:
            detail += f"，误差 {(dist - length) / scale * 100:+.0f}%"
        out.append({"rule": "segment_length", "passed": ok, "detail": detail})
    return out


# ---------------------------------------------------------------------------
# 三角形不等式：最长边 < 另两边之和
# ---------------------------------------------------------------------------


def _triangle_edges(segments: list[dict]) -> list[tuple[str, str, float]] | None:
    """把带 length 的线段规约成"恰好一个三角形"的三条边；否则返回 None。

    判定：恰好 3 条带长度线段、端点覆盖恰好 3 个不同点、且每个点恰好是
    两条线段的端点（三条边闭合构成三角形）。不满足（数据不足/多出线段/退化）→ None。
    """
    edges: list[tuple[str, str, float]] = []
    for seg in segments:
        ep = seg.get("endpoints") or []
        length = seg.get("length")
        if len(ep) != 2 or length is None:
            continue
        try:
            length = float(length)
        except (TypeError, ValueError):
            continue
        edges.append((str(ep[0]), str(ep[1]), length))
    if len(edges) != 3:
        return None
    counts: dict[str, int] = {}
    for a, b, _ in edges:
        counts[a] = counts.get(a, 0) + 1
        counts[b] = counts.get(b, 0) + 1
    if len(counts) != 3 or any(n != 2 for n in counts.values()):
        return None
    return edges


def check_triangle_inequality(
    points: dict[str, list[float]], segments: list[dict]
) -> list[dict]:
    """三角形不等式：三边排序后最长边必须严格小于另两边之和（带极小容差）。

    恰好 3 条带 length 的线段且端点覆盖 3 个不同点（构成一个三角形）时判定，
    否则返回 [] 不判定。可判负：VLM 把 5/7/6 误读成 3/4/9 之类时，
    最长边 ≥ 另两边之和，即抓"读错边长"的幻觉。

    points 参数为后续坐标派生长度预留，当前只使用标注长度。
    """
    edges = _triangle_edges(segments)
    if edges is None:
        return []
    a, b, longest = sorted(length for _, _, length in edges)
    tol = 1e-6 * max(1.0, a, b, longest)
    ok = longest <= a + b + tol
    if ok:
        detail = (
            f"三边 {a:g}/{b:g}/{longest:g}，最长边 {longest:g} < 另两边之和 "
            f"{a + b:g}，三角形不等式成立"
        )
    else:
        detail = (
            f"三边 {a:g}/{b:g}/{longest:g}，最长边 {longest:g} ≥ 另两边之和 "
            f"{a + b:g}，三角形不等式被违反（最长边 ≥ 另两边之和）"
        )
    return [{"rule": "triangle_inequality", "passed": ok, "detail": detail}]


# ---------------------------------------------------------------------------
# 勾股定理：直角三角形的三边长应满足 a² + b² = c²
# ---------------------------------------------------------------------------


def _is_right_angle(ang: dict) -> bool:
    """角的标注值是否 ≈ 90°（容差 1°）。"""
    value = ang.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return abs(float(value) - 90.0) <= 1.0
    except (TypeError, ValueError):
        return False


def _pythagoras_positive_only(edges: list[tuple[str, str, float]]) -> list[dict]:
    """条件 B：无 90° 角声明时的正面确认（不判负）。

    三边恰好构成三角形且某一对短边的平方和 ≈ 最长边平方（20% 相对容差，
    与 check_segment_lengths 风格一致）→ 输出 passed=True 的"可能是直角三角形"；
    普通三角形不满足勾股关系 → 不输出结论（不违反任何规则）。
    """
    a, b, c = sorted(length for _, _, length in edges)
    leg_sq, hyp_sq = a * a + b * b, c * c
    scale = max(1.0, leg_sq, hyp_sq)
    if abs(leg_sq - hyp_sq) > 0.2 * scale:
        return []
    return [
        {
            "rule": "pythagoras",
            "passed": True,
            "detail": (
                f"三边 {a:g}/{b:g}/{c:g} 满足勾股关系"
                f"（{a:g}²+{b:g}²={c:g}²），可能是直角三角形"
            ),
        }
    ]


def check_pythagoras(
    points: dict[str, list[float]], segments: list[dict], angles: list[dict]
) -> list[dict]:
    """勾股定理：直角三角形的三边长应满足 斜边² = 直角边₁² + 直角边₂²。

    条件 A（可判负）：angles 中存在 value ≈ 90°（容差 1°）的角、顶点为 V，
        且三边（V-A、V-B、A-B）都有 length → 校验 |AB| ≈ √(|VA|²+|VB|²)
        （20% 相对容差）；不满足 → passed=False（"标注直角与三边长矛盾"）。
    条件 B（仅正面确认，不判负）：无 90° 角声明但三边构成三角形且满足勾股 →
        输出 passed=True 的"可能是直角三角形"；普通三角形不输出结论。
    条件 C：声明了 ≈90° 但三边数据不全（缺某条边）→ 跳过不判定（返回 []）。
    """
    edges = _triangle_edges(segments)
    if edges is None:
        return []  # 条件 C / 无三角形：数据不足，不判定
    right = [a for a in angles if _is_right_angle(a)]
    if not right:
        return _pythagoras_positive_only(edges)
    out: list[dict] = []
    for ang in right:
        v = ang.get("vertex")
        if not isinstance(v, str) or not v:
            continue  # 直角顶点缺失 → 无法定位直角边，跳过
        legs = [length for (p, q, length) in edges if v in (p, q)]
        hyp = [length for (p, q, length) in edges if v not in (p, q)]
        if len(legs) != 2 or len(hyp) != 1:
            continue  # 顶点不在三角形上 / 三边不全 → 条件 C 不判定
        va, vb = legs[0], legs[1]
        h = hyp[0]
        expected = math.hypot(va, vb)
        scale = max(1.0, h, expected)
        ok = abs(h - expected) <= 0.2 * scale
        if ok:
            detail = (
                f"直角顶点 {v}：两直角边 {va:g}、{vb:g}，斜边 {h:g}，"
                f"√({va:g}²+{vb:g}²)={expected:g} ≈ {h:g} OK"
            )
        else:
            detail = (
                f"直角顶点 {v}：两直角边 {va:g}、{vb:g}，斜边应为 "
                f"√({va:g}²+{vb:g}²)={expected:g} 而非 {h:g}，标注直角与三边长矛盾"
            )
        out.append({"rule": "pythagoras", "passed": ok, "detail": detail})
    return out


# ---------------------------------------------------------------------------
# 圆定理：圆心到圆上点距离 = 半径（circles 提供 on 圆上点列表时）
# ---------------------------------------------------------------------------


def check_circle_radius(points: dict, circles: list[dict]) -> list[dict]:
    """圆上点到圆心的距离 ≈ 半径（20% 相对容差，与 check_segment_lengths 一致）。

    仅当 circles 条目提供 `on`（圆上点列表）且圆心/半径/圆上点坐标齐全时判定；
    数据不足（无 on / 缺坐标）→ 跳过不判定。可判负：VLM 把圆上点坐标或半径读错 → |OP| ≠ r。
    """
    out: list[dict] = []
    for circ in circles:
        center = circ.get("center", "")
        radius = circ.get("radius")
        on = circ.get("on") or []
        if center not in points or radius is None:
            continue
        try:
            radius = float(radius)
        except (TypeError, ValueError):
            continue
        if not on:
            # 圆心/半径齐全但未提供圆上点：无法判定"点在圆上"（语义信息只能来自模型），
            # 不判负（避免把无关点误当圆上点），但输出可见的"未校验"信号，
            # 让上层/agent 知道这条圆定理校验被跳过了（P2-1：circ_adv 静默漏防的根因）。
            out.append(
                {
                    "rule": "circle_radius",
                    "passed": True,
                    "detail": (
                        f"圆 O({center}) r={radius} 未提供圆上点（on），"
                        "『圆上点到圆心距离 = 半径』未校验"
                    ),
                }
            )
            continue
        c = points[center]
        for pname in on:
            if pname not in points:
                continue
            p = points[pname]
            if len(c) < 2 or len(p) < 2:
                continue
            dist = math.hypot(p[0] - c[0], p[1] - c[1])
            scale = max(1.0, dist, radius)
            ok = abs(dist - radius) <= 0.2 * scale
            detail = f"|{center}{pname}| 距离 {dist:g} vs 半径 {radius:g}"
            if not ok:
                detail += f"，误差 {(dist - radius) / scale * 100:+.0f}%（圆上点到圆心距离应等于半径）"
            out.append({"rule": "circle_radius", "passed": ok, "detail": detail})
    return out


def check_diameter_right_angle(
    points: dict, segments: list[dict], angles: list[dict], circles: list[dict]
) -> list[dict]:
    """直径所对的圆周角应为 90°（数学事实，可判负）。

    判定前提（全部满足才判定，否则跳过）：
    - circles 条目提供 on（≥3 个圆上点，含直角顶点与另两点）；
    - angles 中存在 value ≈ 90° 的角，顶点 v 在圆上。
    数学结论：若 ∠v = 90° 且 v、a、b 都在圆上，则 ab 必为直径（|ab| ≈ 2r）。
    - 存在一对圆上点距离 ≈ 2r → passed（正面确认）；
    - 其余所有点对都远离 2r → passed=False（"直径所对圆周角为 90°"被违反）。
    """
    out: list[dict] = []
    for circ in circles:
        center = circ.get("center", "")
        radius = circ.get("radius")
        on = [p for p in (circ.get("on") or []) if p in points and p != center]
        if center not in points or radius is None or len(on) < 3:
            continue
        try:
            radius = float(radius)
        except (TypeError, ValueError):
            continue
        two_r = 2.0 * radius
        for ang in angles:
            if not _is_right_angle(ang):
                continue
            v = ang.get("vertex", "")
            if v not in on:
                continue
            others = [p for p in on if p != v]
            if len(others) < 2:
                continue
            found = False
            for i in range(len(others)):
                for j in range(i + 1, len(others)):
                    a, b = others[i], others[j]
                    d = math.hypot(points[b][0] - points[a][0], points[b][1] - points[a][1])
                    scale = max(1.0, d, two_r)
                    if abs(d - two_r) <= 0.2 * scale:
                        found = True
                        out.append(
                            {
                                "rule": "diameter_right_angle",
                                "passed": True,
                                "detail": (
                                    f"直径 {a}{b} 所对圆周角 ∠{v} = {ang['value']:g}° OK"
                                    f"（{a}{b}={d:g} ≈ 2r={two_r:g}）"
                                ),
                            }
                        )
                        break
                if found:
                    break
            if not found:
                out.append(
                    {
                        "rule": "diameter_right_angle",
                        "passed": False,
                        "detail": (
                            f"∠{v} 标注 {ang['value']:g}°（应为 90° 圆周角），"
                            f"但圆上其余点中没有任何两点距离 ≈ 直径 2r={two_r:g}，"
                            "与『直径所对的圆周角为 90°』矛盾"
                        ),
                    }
                )
        if out:
            break
    return out


# ---------------------------------------------------------------------------
# 相似三角形：两组三角形对应边成比例（仅正面确认，不判负）
# ---------------------------------------------------------------------------


def _split_triangle_pairs(segments: list[dict]):
    """恰好 6 条带长度线段、6 个不同端点、每端点度 2，且构成两个独立三角形。

    返回两个三角形的边长排序列表 [[s1,s2,s3], [t1,t2,t3]]；不满足返回 None。
    """
    edges: list[tuple[str, str, float]] = []
    for seg in segments:
        ep = seg.get("endpoints") or []
        length = seg.get("length")
        if len(ep) != 2 or length is None:
            continue
        try:
            length = float(length)
        except (TypeError, ValueError):
            continue
        edges.append((str(ep[0]), str(ep[1]), length))
    if len(edges) != 6:
        return None
    adj: dict[str, list[tuple[str, float]]] = {}
    for a, b, ln in edges:
        adj.setdefault(a, []).append((b, ln))
        adj.setdefault(b, []).append((a, ln))
    if len(adj) != 6 or any(len(v) != 2 for v in adj.values()):
        return None
    # 从任意点沿度 2 环走，回到起点得第一个三角形
    start = next(iter(adj))
    ring: list[tuple[str, str, float]] = []
    cur, prev = start, None
    for _ in range(12):
        nxts = [x for x in adj[cur] if x[0] != prev]
        if not nxts:
            break
        nb, ln = nxts[0]
        ring.append((cur, nb, ln))
        prev, cur = cur, nb
        if cur == start:
            break
    if len(ring) != 3:
        return None
    used = {tuple(sorted((a, b))) for a, b, _ in ring}
    rest = [e for e in edges if tuple(sorted((e[0], e[1]))) not in used]
    if len(rest) != 3:
        return None
    cnt: dict[str, int] = {}
    for a, b, _ in rest:
        cnt[a] = cnt.get(a, 0) + 1
        cnt[b] = cnt.get(b, 0) + 1
    if len(cnt) != 3 or any(v != 2 for v in cnt.values()):
        return None
    return [sorted(ln for _, _, ln in ring), sorted(ln for _, _, ln in rest)]


def check_similar_proportion(points: dict, segments: list[dict]) -> list[dict]:
    """两组三角形对应边成比例 → 正面确认（passed=True）；其余情况不产生结论。

    只输出正面确认、不判负：任意两个三角形不成比例是正常现象，判负会大面积误报
    （与 check_matrix_multiplicity 同哲学）。detected 供上层诊断。
    """
    tris = _split_triangle_pairs(segments)
    if tris is None:
        return []
    (s1, s2, s3), (t1, t2, t3) = tris
    ratios = [s1 / t1, s2 / t2, s3 / t3]
    lo, hi = min(ratios), max(ratios)
    if hi <= lo * 1.2:  # 三个比例相对偏差 ≤ 20% → 相似
        return [
            {
                "rule": "similar_proportion",
                "passed": True,
                "detail": (
                    f"两组三角形三边 {s1:g}/{s2:g}/{s3:g} 与 {t1:g}/{t2:g}/{t3:g}"
                    f" 对应成比例（≈{lo:g}），可能相似"
                ),
            }
        ]
    return []


# ---------------------------------------------------------------------------
# 平行四边形：对边相等（quads 显式声明时）
# ---------------------------------------------------------------------------


def check_parallelogram(
    points: dict, segments: list[dict], quads: list[dict]
) -> list[dict]:
    """平行四边形（含矩形/菱形/正方形）对边相等；quads 声明了 type 才判定。

    对边 AB/CD、BC/DA 均有 length 标注才校验该对（缺标注跳过，不误报）。
    可判负：VLM 把某条边长读错 → 对边不等。
    """
    out: list[dict] = []
    length: dict[tuple, float] = {}
    for seg in segments:
        ep = seg.get("endpoints") or []
        if len(ep) == 2 and seg.get("length") is not None:
            try:
                length[tuple(sorted(ep))] = float(seg["length"])
            except (TypeError, ValueError):
                continue
    for q in quads or []:
        if q.get("type") not in (
            "parallelogram", "平行四边形", "rectangle", "矩形",
            "rhombus", "菱形", "square", "正方形",
        ):
            continue
        verts = q.get("vertices") or []
        if len(verts) != 4 or not all(v in points for v in verts):
            continue
        a, b, c, d = verts
        for (x1, y1), (x2, y2) in (((a, b), (c, d)), ((b, c), (d, a))):
            l1 = length.get(tuple(sorted((x1, y1))))
            l2 = length.get(tuple(sorted((x2, y2))))
            if l1 is None or l2 is None:
                continue
            scale = max(1.0, l1, l2)
            ok = abs(l1 - l2) <= 0.2 * scale
            detail = f"{x1}{y1}={l1:g} vs {x2}{y2}={l2:g}（{q.get('type')} 对边应相等）"
            if not ok:
                detail += f"，误差 {(l1 - l2) / scale * 100:+.0f}%"
            out.append({"rule": "parallelogram", "passed": ok, "detail": detail})
    return out


# ---------------------------------------------------------------------------
# 函数图：抛物线对称轴 = 两零点中点（命名约定 z1/z2… 与 vertex/V）
# ---------------------------------------------------------------------------


def check_function_symmetry(points: dict) -> list[dict]:
    """抛物线对称轴应过两零点中点（命名约定：零点 z1/z2…，顶点 vertex 或 V）。

    仅当满足命名约定（≥2 个 z 开头且 y≈0 的点 + 名为 vertex/V 的点）时判定，
    否则跳过（数据不足不判定，避免把普通点集误判为抛物线）。
    """
    zeros = [
        name
        for name, p in points.items()
        if name.lower().startswith("z") and len(p) >= 2 and abs(p[1]) <= 0.5
    ]
    vertex = next((name for name in points if name.lower() in ("vertex", "v")), None)
    if len(zeros) < 2 or vertex is None:
        return []
    zx = [points[n][0] for n in zeros]
    vx = points[vertex][0]
    mid = (min(zx) + max(zx)) / 2
    scale = max(1.0, abs(vx), abs(mid))
    ok = abs(vx - mid) <= 0.2 * scale
    detail = f"对称轴 x={vx:g} vs 两零点中点 x={mid:g}"
    if not ok:
        detail += f"，误差 {(vx - mid) / scale * 100:+.0f}%（抛物线对称轴应过两零点中点）"
    return [{"rule": "function_symmetry", "passed": ok, "detail": detail}]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_geometry_checks(data: dict) -> list[dict]:
    """对抽取的图数据执行全部可用的几何/代数检查。

    data 形如 figures/FigureData.to_dict()：可为：
        {"vectors": {...}, "matrices": {...}}  （至少其一）
    也可含 points / segments / figures 图元（1.1 DOM，参与线段长度与角度/半径校验）。
    返回 list[check]。
    """
    checks: list[dict] = []
    pre = _with_vectors(data)
    vectors = pre["vectors"]
    scalars = pre["scalars"]

    t = check_triangle_sum(scalars)
    if t:
        checks.append(t)
    checks.extend(check_vector_sums(vectors))
    checks.extend(check_matrix_multiplicity(data.get("matrices", {})))
    checks.extend(check_segment_lengths(data.get("points", {}), data.get("segments", [])))
    checks.extend(check_triangle_inequality(data.get("points", {}), data.get("segments", [])))
    checks.extend(
        check_pythagoras(
            data.get("points", {}),
            data.get("segments", []),
            (data.get("figures") or {}).get("angles", []),
        )
    )
    # A2 扩充：圆定理 / 相似 / 平行四边形 / 函数对称
    checks.extend(
        check_circle_radius(data.get("points", {}), (data.get("figures") or {}).get("circles", []))
    )
    checks.extend(
        check_diameter_right_angle(
            data.get("points", {}),
            data.get("segments", []),
            (data.get("figures") or {}).get("angles", []),
            (data.get("figures") or {}).get("circles", []),
        )
    )
    checks.extend(check_similar_proportion(data.get("points", {}), data.get("segments", [])))
    checks.extend(
        check_parallelogram(
            data.get("points", {}),
            data.get("segments", []),
            (data.get("figures") or {}).get("quads", []),
        )
    )
    checks.extend(check_function_symmetry(data.get("points", {})))
    checks.extend(check_plausible_ranges(vectors, scalars))
    # 去重：同 rule + 同 detail 只留一条
    seen = set()
    deduped = []
    for c in checks:
        key = (c["rule"], c["detail"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def overall_consistent(checks: list[dict]) -> bool:
    """所有几何检查是否通过（没有 passed=False 且无矛盾）。"""
    return all(c["passed"] for c in checks)


def _fmt(v: list[float]) -> str:
    return "[" + ", ".join(f"{x:g}" for x in v) + "]"
