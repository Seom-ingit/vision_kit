"""结构化图区数据：图元 DOM + 向量/矩阵解析 + 维度一致性校验 + 几何自洽校验 + 渲染回写（纯逻辑）。

解析策略：**JSON 优先、正则兜底** 双层解析 —— 优先提取模型输出里的 JSON 对象，
把 `figures`（points / segments / angles / circles）与 `vectors` / `matrices` 结构化；
JSON 不可用时退化为正则容错（兼容旧格式与模型输出的残缺 JSON）。

1.1 图元 DOM（Diagram Object Model）：
    points    {"A": [x, y], "B": [x, y]}
    segments  [{"endpoints": ["A", "B"], "length": 4}, ...]
    angles    [{"vertex": "A", "sides": ["AB", "AC"], "value": 48}, ...]
    circles   [{"center": "O", "radius": 2.5}, ...]
  所有图元引用（线段端点 / 角顶点 / 圆圆心）必须指向已定义的 points，否则 `dom_consistent=False`。

1.4 渲染回写（round-trip）：
   render_image(path) 用 PIL 把抽取结果还原成 PNG，供人 / 模型目视比对抓幻觉。
"""

import json
import re

from . import geometry

_TYPE_RE = re.compile(r'"type"\s*:\s*"([^"]*)"')
_NOTE_RE = re.compile(r'"note"\s*:\s*"([^"]*)"')
# 数字字符类支持 +/-、小数点、科学计数法（1e-3 / 1E-3），否则模型输出 +1 或 1e-3 时整组向量会被丢弃
_NUM_CLS = r"[-+0-9.eE\s,]+"
_VEC_RE = re.compile(r'"([^"]+)"\s*:\s*\[\s*(' + _NUM_CLS + r")\s*\]")
_MAT_RE = re.compile(r'"([^"]+)"\s*:\s*\[\s*((?:\[\s*' + _NUM_CLS + r"\]\s*,?\s*)+)\s*\]")
_ROW_RE = re.compile(r"\[(" + _NUM_CLS + r")\]")
# 正则兜底时只尝试解析 points（线段/角/圆是对象列表，正则易碎，依赖 JSON 主解析）
_POINTS_BLOCK_RE = re.compile(r'"points"\s*:\s*\{([^}]*)\}')
_PT_RE = re.compile(r'"([^"]+)"\s*:\s*\[\s*([-+0-9.eE\s,]+)\s*\]')

# JSON 主解析时顶层不当作向量/矩阵收集的保留字段
_RESERVED = {
    "type",
    "note",
    "figures",
    "vectors",
    "matrices",
    "quantities",
    "labels",
    "text",
    "raw",
}


class FigureData:
    """从模型响应文本中提取的结构化图区信息（JSON 优先、正则兜底）。

    支持两类结构（向后兼容，只增不删）：
    - 向量：  "a1": [1, 1, 2, 2]
    - 矩阵：  "A": [[2, -1, 1], [1, 4, -2]]
    以及 1.1 图元 DOM（可选）：
    - 图元：  "figures": {"points": ..., "segments": ..., "angles": ..., "circles": ...}
    """

    def __init__(self, text: str):
        self.raw_text = text
        m = _TYPE_RE.search(text)
        self.type = m.group(1).strip() if m else ""
        n = _NOTE_RE.search(text)
        self.note = n.group(1).strip() if n else ""
        self.vectors: dict[str, list[float]] = {}
        self.matrices: dict[str, list[list[float]]] = {}
        self.points: dict[str, list[float]] = {}
        self.segments: list[dict] = []
        self.angles: list[dict] = []
        self.circles: list[dict] = []
        self.quads: list[dict] = []
        self._parse(text)
        # 几何自洽校验：在维度一致之上，用确定性规则检验抽取结果是否自洽
        self.geo_checks: list[dict] = geometry.run_geometry_checks(self.to_geo_dict())
        # 2.3 诊断：本次识别用了多少次模型调用（由 engine.recognize 写入；直连构造时为空）
        self.attempts: int | None = None

    # ------------------------------------------------------------------ 解析

    @classmethod
    def merge(cls, parts: list["FigureData"]) -> "FigureData":
        """把分块识别的多个 FigureData 合并为一个，再做跨块一致性/几何校验（A3）。

        合并规则（跨块对象在边缘块可能被截断）：
        - 向量：同名取分量数最多的版本（截断块分量少）；分量数相同取第一个；
        - 矩阵：同名取行数最多的版本；
        - 点 / 线段 / 角 / 圆 / 四边形：同名或同内容去重取第一个；
        - note：各块拼接；type：取第一个非空。
        合并后重新跑 geo_checks（跨块几何关系也能被校验）。
        """
        merged = cls("")
        merged.type = next((p.type for p in parts if p.type), "")
        merged.note = "；".join(n for p in parts if (n := p.note))
        merged.vectors = _merge_longest(
            [p.vectors for p in parts], key=len
        )
        merged.matrices = _merge_longest(
            [p.matrices for p in parts], key=lambda rows: len(rows)
        )
        merged.points = _merge_first([p.points for p in parts])
        merged.segments = _merge_dedup([p.segments for p in parts])
        merged.angles = _merge_dedup([p.angles for p in parts])
        merged.circles = _merge_dedup([p.circles for p in parts])
        merged.quads = _merge_dedup([p.quads for p in parts])
        # 合并后重新做几何自洽校验（A3：跨块向量/几何关系）
        merged.geo_checks = geometry.run_geometry_checks(merged.to_geo_dict())
        return merged

    def _parse(self, text: str) -> None:
        """JSON 优先、正则兜底。"""
        obj = _extract_json(text)
        if isinstance(obj, dict) and self._parse_json(obj):
            return  # JSON 主解析成功：跳过正则，避免把 figures 里的点坐标误解析成向量
        self._parse_regex(text)

    def _parse_json(self, obj: dict) -> bool:
        """从 JSON 对象解析 type/note/figures/vectors/matrices；返回是否解析到相关内容。"""
        found = False
        for key in ("type", "note"):
            v = obj.get(key)
            if isinstance(v, str):
                setattr(self, key, v.strip())
                found = True
        figs = obj.get("figures")
        if isinstance(figs, dict):
            p = _parse_points(figs.get("points"))
            if p is not None:
                self.points = p
                found = True
            s = _parse_segments(figs.get("segments"))
            if s is not None:
                self.segments = s
                found = True
            a = _parse_angles(figs.get("angles"))
            if a is not None:
                self.angles = a
                found = True
            c = _parse_circles(figs.get("circles"))
            if c is not None:
                self.circles = c
                found = True
            q = _parse_quads(figs.get("quads"))
            if q is not None:
                self.quads = q
                found = True
            # 模型输出格式漂移兜底：vectors/matrices 也可能被嵌套进 figures 块
            # （提示词要求放顶层，但实测 qwen3-vl-flash 有时会放错位置）。
            # 此处先收集，顶层同名值随后 update 覆盖 → 顶层（规范位置）优先。
            for wrapper in ("vectors", "matrices"):
                w = figs.get(wrapper)
                if isinstance(w, dict):
                    fv, fm = _collect_named(w)
                    if fv:
                        self.vectors.update(fv)
                        found = True
                    if fm:
                        self.matrices.update(fm)
                        found = True
        for wrapper in ("vectors", "matrices", "quantities"):
            w = obj.get(wrapper)
            if isinstance(w, dict):
                v, m = _collect_named(w)
                if v:
                    self.vectors.update(v)
                    found = True
                if m:
                    self.matrices.update(m)
                    found = True
        # 旧格式兼容：顶层裸数组（非保留字段）按向量/矩阵收录
        v, m = _collect_named(
            {k: val for k, val in obj.items() if k not in _RESERVED and isinstance(val, list)}
        )
        if v:
            self.vectors.update(v)
            found = True
        if m:
            self.matrices.update(m)
            found = True
        return found

    def _parse_regex(self, text: str) -> None:
        """正则兜底：向量 / 矩阵 / points（与旧版行为一致，图元仅尝试 points）。"""
        for m in _MAT_RE.finditer(text):
            name = m.group(1).strip()
            rows = []
            for rm in _ROW_RE.finditer(m.group(2)):
                nums = _nums(rm.group(1))
                if not nums:
                    rows = []
                    break
                rows.append(nums)
            if rows and all(len(r) == len(rows[0]) for r in rows):
                self.matrices[name] = rows
        for m in _VEC_RE.finditer(text):
            name = m.group(1).strip()
            if name in self.matrices:
                continue
            nums = _nums(m.group(2))
            if nums:
                self.vectors[name] = nums
        pm = _POINTS_BLOCK_RE.search(text)
        if pm:
            pts = {}
            for mm in _PT_RE.finditer(pm.group(1)):
                nums = _nums(mm.group(2))
                if len(nums) >= 2:
                    pts[mm.group(1).strip()] = nums[:2]
            if pts:
                self.points = pts

    # ------------------------------------------------------------------ 校验

    @property
    def consistent(self) -> bool:
        """同组向量长度、同组矩阵形状、图元引用必须一致（≥2 项时逐项比对，防漏行防漏元素）。"""
        if not _shape_consistent(self.vectors):
            return False
        if not _shape_consistent(self.matrices):
            return False
        if not self.dom_consistent:
            return False
        return True

    @property
    def dom_consistent(self) -> bool:
        """图元引用完整性：线段端点 / 角顶点 / 圆圆心必须存在于 points；角的边须可解析。

        无任何图元时视为一致（兼容纯向量/矩阵图，向后兼容）。
        """
        if not (self.points or self.segments or self.angles or self.circles or self.quads):
            return True
        ok = True
        for seg in self.segments:
            for p in seg.get("endpoints", []):
                if p not in self.points:
                    ok = False
        for ang in self.angles:
            if ang.get("vertex", "") not in self.points:
                ok = False
            for side in ang.get("sides", []):
                # 边的引用形态："AB"（两点构成）或单点 "A"（射线端点）
                if side in self.points:
                    continue
                if len(side) == 2 and side[0] in self.points and side[1] in self.points:
                    continue
                ok = False
        for c in self.circles:
            if c.get("center", "") not in self.points:
                ok = False
        for q in self.quads:
            for v in q.get("vertices", []):
                if v not in self.points:
                    ok = False
        return ok

    @property
    def has_content(self) -> bool:
        return bool(
            self.vectors
            or self.matrices
            or self.points
            or self.segments
            or self.angles
            or self.circles
            or self.quads
        )

    # ------------------------------------------------------------------ 诊断（2.3）

    @property
    def error_code(self) -> str:
        """失败诊断错误码（成功为 OK）。优先级：维度 > 几何 > 内容 > 解析。

        - DIMENSION_MISMATCH：同组向量/矩阵维度不一致（可能漏数）
        - GEOMETRY_INCONSISTENT：维度一致但几何/代数不自洽（可能读数有误）
        - OK：解析出数值内容
        - NO_NUMERIC_CONTENT：无数值但有 type/note（纯文本图，合法结果）
        - PARSE_FAILED：既无内容也无说明（无法解析）
        """
        if not self.consistent:
            return "DIMENSION_MISMATCH"
        if any(not c["passed"] for c in self.geo_checks):
            return "GEOMETRY_INCONSISTENT"
        if self.has_content:
            return "OK"
        if self.type or self.note:
            return "NO_NUMERIC_CONTENT"
        return "PARSE_FAILED"

    @property
    def confidence(self) -> str:
        """整体置信度：high / medium / low。

        - low   ：维度不一致 / 几何不自洽 / 无数值内容（读数不可信）
        - medium：全部校验通过，但经过补漏重试（attempts > 1）
        - high  ：首轮贪心直出且全部校验通过
        """
        if not self.has_content:
            return "low"
        if not self.consistent or any(not c["passed"] for c in self.geo_checks):
            return "low"
        if self.attempts and self.attempts > 1:
            return "medium"
        return "high"

    @property
    def item_confidence(self) -> dict[str, str]:
        """逐标注置信度：向量/矩阵/点 名字 -> high/medium/low。

        出现在未通过校验 detail 中的名字降为 low；其余继承整体置信度。
        """
        failed_names = _names_in_checks([c for c in self.geo_checks if not c["passed"]])
        base = "high" if self.confidence == "high" else "medium"
        out: dict[str, str] = {}
        for name in list(self.vectors) + list(self.matrices) + list(self.points):
            out[name] = "low" if name in failed_names else base
        return out

    def diagnostics(self) -> dict:
        """诊断信息（2.3）：错误码 / 置信度 / 重试次数 / 几何失败明细。"""
        return {
            "error_code": self.error_code,
            "confidence": self.confidence,
            "item_confidence": self.item_confidence,
            "retried": bool(self.attempts and self.attempts > 1),
            "attempts": self.attempts if self.attempts else 1,
            "geo_failures": [c for c in self.geo_checks if not c["passed"]],
        }

    def to_geo_dict(self) -> dict:
        """供几何校验使用的数据视图：向量 + 矩阵 + 图元（原始 float）。"""
        return {
            "vectors": dict(self.vectors),
            "matrices": dict(self.matrices),
            "points": dict(self.points),
            "segments": [dict(s) for s in self.segments],
            "figures": {
                "angles": [dict(a) for a in self.angles],
                "circles": [dict(c) for c in self.circles],
                "quads": [dict(q) for q in self.quads],
            },
        }

    # ------------------------------------------------------------------ 渲染

    def render(self) -> str:
        parts = []
        if self.type:
            parts.append(f"图形类型：{self.type}")
        for k, rows in self.matrices.items():
            rendered_rows = ", ".join(
                "[" + ", ".join(_fmt_num(x) for x in row) + "]" for row in rows
            )
            parts.append(f"{k}=[{rendered_rows}]")
        for k, v in self.vectors.items():
            fmt = ", ".join(_fmt_num(x) for x in v)
            parts.append(f"{k}=[{fmt}]")
        for name, pos in self.points.items():
            parts.append(f"点{name}({_fmt_num(pos[0])},{_fmt_num(pos[1])})")
        for seg in self.segments:
            ep = "".join(seg.get("endpoints", []))
            length = seg.get("length")
            s = f"线段{ep}"
            if length is not None:
                s += f" 长={_fmt_num(length)}"
            parts.append(s)
        for ang in self.angles:
            v = ang.get("vertex", "")
            sides = "/".join(ang.get("sides", []))
            s = f"角{v}({sides})"
            if ang.get("value") is not None:
                s += f"={_fmt_num(ang['value'])}°"
            parts.append(s)
        for c in self.circles:
            s = f"圆O({c.get('center', '')})"
            if c.get("radius") is not None:
                s += f" r={_fmt_num(c['radius'])}"
            if c.get("on"):
                s += " 圆上点:" + ",".join(str(p) for p in c["on"])
            parts.append(s)
        for q in self.quads:
            verts = "".join(q.get("vertices", []))
            parts.append(f"{q.get('type', '四边形')}{verts}")
        if self.note:
            parts.append(self.note)
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict:
        """结构化输出：整数化 + 图元 DOM + 渲染文本 + 原始文本 + 校验结果（向后兼容，只增不删）。"""
        return {
            "type": self.type,
            "vectors": {k: _num_list(v) for k, v in self.vectors.items()},
            "matrices": {k: [_num_list(row) for row in rows] for k, rows in self.matrices.items()},
            "figures": {
                "points": {k: _num_list(v) for k, v in self.points.items()},
                "segments": [_num_seg(s) for s in self.segments],
                "angles": [_num_ang(a) for a in self.angles],
                "circles": [_num_circ(c) for c in self.circles],
                "quads": [_num_quad(q) for q in self.quads],
            },
            "consistent": self.consistent,
            "dom_consistent": self.dom_consistent,
            "note": self.note,
            "geo_checks": self.geo_checks,
            "attempts": self.attempts if self.attempts else 1,
            "diagnostics": self.diagnostics(),
            "text": self.render(),
            "raw": self.raw_text.strip(),
        }

    # ------------------------------------------------------------------ TikZ 输出（1.5）

    def to_tikz(self) -> str | None:
        """DOM → 可编译的 TikZ 片段（LaTeX），供「图→代码」复刻 / 排版。

        - 有 points/segments/angles/circles → 几何图（\\coordinate + \\draw）；
        - 有 vectors → 箭头（2 分量从原点出发，4 分量起终点）；
        - 两者皆无（纯矩阵/纯文本）→ 返回 None（无可绘制内容）。
        """
        if not (self.points or self.segments or self.angles or self.circles or self.vectors):
            return None
        lines = ["\\begin{tikzpicture}[scale=0.8, >=stealth]"]
        for name, pos in self.points.items():
            lines.append(f"  \\coordinate ({name}) at ({_fmt_num(pos[0])}, {_fmt_num(pos[1])});")
        for seg in self.segments:
            ep = seg.get("endpoints", [])
            if len(ep) != 2 or ep[0] not in self.points or ep[1] not in self.points:
                continue
            label = ""
            if seg.get("length") is not None:
                label = f" node[midway,above] {{{_fmt_num(seg['length'])}}}"
            lines.append(f"  \\draw ({ep[0]}) -- ({ep[1]}){label};")
        for ang in self.angles:
            v = ang.get("vertex", "")
            sides = ang.get("sides", [])
            if v not in self.points or len(sides) < 2 or ang.get("value") is None:
                continue
            far1 = _side_far_point(sides[0], v, self.points)
            far2 = _side_far_point(sides[1], v, self.points)
            if far1 is None or far2 is None:
                continue
            pv = self.points[v]
            lines.append(
                f"  \\draw ({_fmt_num(pv[0])}, {_fmt_num(pv[1])}) -- "
                f"({_fmt_num(far1[0])}, {_fmt_num(far1[1])});"
            )
            lines.append(
                f"  \\draw ({_fmt_num(pv[0])}, {_fmt_num(pv[1])}) -- "
                f"({_fmt_num(far2[0])}, {_fmt_num(far2[1])}) "
                f"node[midway,left] {{{_fmt_num(ang['value'])}$^\\circ$}};"
            )
        for c in self.circles:
            center = c.get("center", "")
            r = c.get("radius")
            if center in self.points and r is not None:
                lines.append(f"  \\draw ({center}) circle ({_fmt_num(r)});")
        for name in self.points:
            lines.append(f"  \\fill ({name}) circle (1.5pt);")
            lines.append(f"  \\node[above right] at ({name}) {{{name}}};")
        for name, v in self.vectors.items():
            if len(v) == 2:
                lines.append(
                    f"  \\draw[->] (0,0) -- ({_fmt_num(v[0])}, {_fmt_num(v[1])}) "
                    f"node[midway,above] {{{name}}};"
                )
            elif len(v) >= 4:
                lines.append(
                    f"  \\draw[->] ({_fmt_num(v[0])}, {_fmt_num(v[1])}) -- "
                    f"({_fmt_num(v[2])}, {_fmt_num(v[3])}) node[midway,above] {{{name}}};"
                )
        lines.append("\\end{tikzpicture}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 渲染回写（1.4）

    def render_image(self, path: str, size: int = 800, margin: int = 60) -> str | None:
        """把抽取结果还原成 PNG（round-trip），供人 / 模型目视比对抓幻觉。

        - 有图元（points）时按坐标画点 / 线段 / 角 / 圆，向量以箭头叠加在同一坐标系；
        - 纯向量/矩阵图（无 points）按向量端点坐标画箭头；
        - 两者皆无但 render() 有文本时，把渲染文本画成图片（文本回写）。
        返回写入的路径；完全无可绘制内容时返回 None。
        依赖 PIL（项目已有），延迟导入避免拖慢普通路径。
        """
        from PIL import Image, ImageDraw  # noqa: PLC0415

        font = load_font(20)
        img = Image.new("RGB", (size, size), "white")
        draw = ImageDraw.Draw(img)
        to_xy = _map_coords(self.points, size, margin)
        if to_xy is not None:
            self._draw_geometry(draw, to_xy, font)
        elif self.vectors:
            to_xy = _map_coords(_vector_endpoints(self.vectors), size, margin)
            if to_xy is not None:
                self._draw_vectors(draw, to_xy, font)
        if not self._drawn_anything(draw):
            # 文本回写：把 render() 画成图，至少保证 round-trip 有可见产物
            text = self.render()
            if not text:
                return None
            draw.multiline_text((margin, margin), text, fill="black", font=font)
        img.save(path, format="PNG")
        return path

    def _draw_geometry(self, draw, to_xy, font) -> None:
        """画图元 DOM：线段 / 角 / 圆 / 点，并叠加向量箭头。"""
        for seg in self.segments:
            ep = seg.get("endpoints", [])
            if len(ep) == 2 and ep[0] in self.points and ep[1] in self.points:
                p0 = to_xy(*self.points[ep[0]])
                p1 = to_xy(*self.points[ep[1]])
                draw.line([p0, p1], fill="black", width=2)
                if seg.get("length") is not None:
                    mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
                    draw.text(
                        (mid[0] + 4, mid[1] - 12),
                        f"{_fmt_num(seg['length'])}",
                        fill="blue",
                        font=font,
                    )
        for ang in self.angles:
            v = ang.get("vertex", "")
            sides = ang.get("sides", [])
            if v not in self.points or not sides:
                continue
            pv = to_xy(*self.points[v])
            tips = []
            for side in sides:
                far = _side_far_point(side, v, self.points)
                if far is None:
                    continue
                tips.append(to_xy(*far))
                draw.line([pv, tips[-1]], fill="black", width=2)
            if len(tips) >= 2 and ang.get("value") is not None:
                # 近似画角弧：以顶点为圆心，在两条边上采样若干点连线
                r = 24
                pts = []
                for t in range(0, 11):
                    frac = t / 10.0
                    x = pv[0] + (tips[0][0] - pv[0]) * frac
                    y = pv[1] + (tips[0][1] - pv[1]) * frac
                    if abs(x - pv[0]) < 1e-6 and abs(y - pv[1]) < 1e-6:
                        continue
                    dist = ((x - pv[0]) ** 2 + (y - pv[1]) ** 2) ** 0.5
                    if dist < 1e-6:
                        continue
                    sx = pv[0] + (x - pv[0]) / dist * r
                    sy = pv[1] + (y - pv[1]) / dist * r
                    pts.append((sx, sy))
                if len(pts) >= 2:
                    draw.line(pts, fill="red", width=1)
                    draw.text(
                        (pts[0][0] + 2, pts[0][1] + 2),
                        f"{_fmt_num(ang['value'])}°",
                        fill="red",
                        font=font,
                    )
        for c in self.circles:
            center = c.get("center", "")
            if center not in self.points:
                continue
            cx, cy = to_xy(*self.points[center])
            r = c.get("radius")
            if r is None:
                continue
            radius_px = _radius_px(self.points[center], r, to_xy)
            draw.ellipse(
                [cx - radius_px, cy - radius_px, cx + radius_px, cy + radius_px],
                outline="black",
                width=2,
            )
            draw.text((cx + 4, cy - radius_px - 14), f"r={_fmt_num(r)}", fill="blue", font=font)
        self._draw_vectors(draw, to_xy, font)
        for name, pos in self.points.items():
            x, y = to_xy(*pos)
            r = 4
            draw.ellipse([x - r, y - r, x + r, y + r], fill="black", outline="black")
            draw.text((x + 4, y - 12), str(name), fill="black", font=font)

    def _draw_vectors(self, draw, to_xy, font) -> None:
        """画向量箭头：2 元素分量 [dx, dy] 从原点出发；4 元素 [x1,y1,x2,y2] 起终点。"""
        for name, v in self.vectors.items():
            if len(v) == 2:
                p0 = to_xy(0.0, 0.0)
                p1 = to_xy(v[0], v[1])
            elif len(v) >= 4:
                p0 = to_xy(v[0], v[1])
                p1 = to_xy(v[2], v[3])
            else:
                continue
            _draw_arrow(draw, p0, p1)
            draw.text(
                ((p0[0] + p1[0]) / 2 + 4, (p0[1] + p1[1]) / 2 - 14),
                str(name),
                fill="green",
                font=font,
            )

    def _drawn_anything(self, draw) -> bool:
        return bool(self.points or self.segments or self.angles or self.circles or self.vectors)


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict | None:
    """从模型输出中提取第一个平衡的 JSON 对象；失败返回 None（容忍 ```json 围栏与前后杂质）。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _collect_named(data: dict) -> tuple[dict, dict]:
    """把 name -> 数字列表 / 数字列表的列表 分类为 (向量, 矩阵)；无法识别则忽略。"""
    vectors: dict[str, list[float]] = {}
    matrices: dict[str, list[list[float]]] = {}
    for name, value in data.items():
        if not isinstance(value, list) or not value:
            continue
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
            vectors[str(name)] = [float(x) for x in value]
            continue
        if (
            all(
                isinstance(r, list)
                and r
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in r)
                for r in value
            )
            and len({len(r) for r in value}) == 1
        ):
            matrices[str(name)] = [[float(x) for x in r] for r in value]
    return vectors, matrices


def _parse_points(value) -> dict[str, list[float]] | None:
    """points 支持两种形态：{"A": [x, y]} 或 {"A": {"pos": [x, y]}}。"""
    if not isinstance(value, dict):
        return None
    out: dict[str, list[float]] = {}
    for name, v in value.items():
        if isinstance(v, dict) and isinstance(v.get("pos"), list):
            v = v["pos"]
        nums = _nums_of(v)
        if nums is not None and len(nums) >= 2:
            out[str(name)] = nums[:2]
    return out or None


def _parse_segments(value) -> list[dict] | None:
    """segments: [{"endpoints": ["A", "B"], "length": 4}, ...]（length 可选）。"""
    if not isinstance(value, list):
        return None
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        ep = item.get("endpoints")
        if not isinstance(ep, list) or len(ep) != 2 or not all(isinstance(x, str) for x in ep):
            continue
        seg = {"endpoints": [ep[0], ep[1]]}
        length = item.get("length")
        if isinstance(length, (int, float)) and not isinstance(length, bool):
            seg["length"] = float(length)
        out.append(seg)
    return out or None


def _parse_angles(value) -> list[dict] | None:
    """angles: [{"vertex": "A", "sides": ["AB", "AC"], "value": 48}, ...]（sides/value 可选）。"""
    if not isinstance(value, list):
        return None
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("vertex"), str):
            continue
        ang: dict = {"vertex": item["vertex"]}
        sides = item.get("sides")
        if isinstance(sides, list) and all(isinstance(s, str) for s in sides):
            ang["sides"] = list(sides)
        val = item.get("value")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            ang["value"] = float(val)
        out.append(ang)
    return out or None


def _parse_circles(value) -> list[dict] | None:
    """circles: [{"center": "O", "radius": 2.5, "on": ["P", "Q"]}, ...]（radius/on 可选）。"""
    if not isinstance(value, list):
        return None
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("center"), str):
            continue
        c: dict = {"center": item["center"]}
        r = item.get("radius")
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            c["radius"] = float(r)
        on = item.get("on")
        if isinstance(on, list) and all(isinstance(s, str) for s in on):
            c["on"] = list(on)
        out.append(c)
    return out or None


def _parse_quads(value) -> list[dict] | None:
    """quads: [{"vertices": ["A","B","C","D"], "type": "parallelogram"}, ...]（type 可选）。"""
    if not isinstance(value, list):
        return None
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        verts = item.get("vertices")
        if not isinstance(verts, list) or len(verts) != 4 or not all(
            isinstance(s, str) for s in verts
        ):
            continue
        q: dict = {"vertices": list(verts)}
        typ = item.get("type")
        if isinstance(typ, str) and typ:
            q["type"] = typ
        out.append(q)
    return out or None


def _merge_longest(groups: list[dict], key) -> dict:
    """合并多个 name -> 值 字典：同名取 key(值) 最大的版本（分块边缘截断块自然被覆盖）。"""
    out: dict = {}
    for group in groups:
        for name, value in group.items():
            cur = out.get(name)
            if cur is None or key(value) > key(cur):
                out[name] = value
    return out


def _merge_first(groups: list[dict]) -> dict:
    """合并多个 name -> 值 字典：同名取第一个（重叠区坐标一致时等价）。"""
    out: dict = {}
    for group in groups:
        for name, value in group.items():
            if name not in out:
                out[name] = value
    return out


def _merge_dedup(groups: list[list]) -> list:
    """合并多个对象列表：按 json 序列化去重（重叠区的同一对象只留一份）。"""
    out: list = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _nums_of(value) -> list[float] | None:
    """数字列表 → float 列表；含非数字返回 None。"""
    if not isinstance(value, list) or not value:
        return None
    out: list[float] = []
    for x in value:
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            return None
        out.append(float(x))
    return out


def _nums(body: str) -> list[float]:
    """把 '2, -1, 1.5' 解析为 float 列表；含非数字返回 []"""
    out = []
    for tok in body.replace(",", " ").split():
        try:
            out.append(float(tok))
        except ValueError:
            return []
    return out


def _shape_key(v: list) -> tuple:
    """对象形状键：向量 → (长度,)，矩阵 → (行数, 列数)；空列表/行列不齐 → 异常形状"""
    if not v:
        return ()
    first = v[0]
    if isinstance(first, (list, tuple)):
        # 行列不齐的“矩阵”视为异常形状，避免与正常矩阵形状混淆
        if not all(len(r) == len(first) for r in v):
            return (len(v), "ragged")
        return (len(v), len(first))
    return (len(v),)


def _shape_consistent(items: dict) -> bool:
    """同组向量长度、同组矩阵形状必须完全一致（≥2 项时逐项比对，防漏行防漏元素）。

    原先的“多数一致”启发式在组内只有 2 个对象时永不判不一致，
    恰好漏掉最典型的漏数场景（如 a1 4 维、b 3 维），已改为严格一致。
    """
    if len(items) < 2:
        return True
    shapes = {_shape_key(v) for v in items.values()}
    return len(shapes) == 1


def _names_in_checks(checks: list[dict]) -> set[str]:
    """从未通过的 check detail 中提取可能相关的标注名（供 2.3 逐值置信度降级）。

    提取规则：∠ 后的字母（角度）、全部 [A-Za-z_][A-Za-z0-9_]* 标识符（a1/b2/M11/AB/单字母）。
    detail 均为中文叙述（无英文单词），因此单字母 token 只可能来自标注名本身
    （如向量 b、点 A），不会因英文介词误伤。
    """
    names: set[str] = set()
    for c in checks:
        detail = c.get("detail", "")
        for m in re.finditer(r"∠([A-Za-zα-ωΑ-Ω0-9_]+)", detail):
            names.add(m.group(1))
        for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", detail):
            names.add(m.group(0))
    return names


def _fmt_num(x: float) -> str:
    return str(int(x)) if x.is_integer() else str(x)


def _num_list(v: list[float]) -> list:
    """float 列表转成 int/float 混合（整数值用 int，更友好）"""
    return [int(x) if x.is_integer() else x for x in v]


def _num_seg(seg: dict) -> dict:
    out = {"endpoints": list(seg.get("endpoints", []))}
    if seg.get("length") is not None:
        out["length"] = _intify(seg["length"])
    return out


def _num_ang(ang: dict) -> dict:
    out: dict = {"vertex": ang.get("vertex", "")}
    if ang.get("sides") is not None:
        out["sides"] = list(ang["sides"])
    if ang.get("value") is not None:
        out["value"] = _intify(ang["value"])
    return out


def _num_circ(c: dict) -> dict:
    out: dict = {"center": c.get("center", "")}
    if c.get("radius") is not None:
        out["radius"] = _intify(c["radius"])
    if c.get("on") is not None:
        out["on"] = list(c["on"])
    return out


def _num_quad(q: dict) -> dict:
    out: dict = {"vertices": list(q.get("vertices", []))}
    if q.get("type"):
        out["type"] = q["type"]
    return out


def _intify(x: float):
    return int(x) if float(x).is_integer() else float(x)


# ---------------------------------------------------------------------------
# 渲染回写辅助（1.4）
# ---------------------------------------------------------------------------


_FONT_CANDIDATES = (
    "msyh.ttc",
    "msyhbd.ttc",
    "simhei.ttf",
    "arial.ttf",
    "Arial.ttf",
    "DejaVuSans.ttf",
    "NotoSansCJK-Regular.ttc",
)


def load_font(px: int):
    """加载可用字体；找不到候选字体时退回 PIL 默认字体（数字/字母仍可渲染）。"""
    for name in _FONT_CANDIDATES:
        try:
            from PIL import ImageFont  # noqa: PLC0415

            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        from PIL import ImageFont  # noqa: PLC0415

        return ImageFont.load_default(px)
    except TypeError:
        from PIL import ImageFont  # noqa: PLC0415

        return ImageFont.load_default()


def _map_coords(points: dict, size: int, margin: int):
    """把抽象坐标映射到画布像素（居中 + 翻转 y 轴）；无点返回 None。"""
    if not points:
        return None
    xs = [p[0] for p in points.values()]
    ys = [p[1] for p in points.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0
    span = max(span_x, span_y)
    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    half = span / 2 * 1.3  # 四周留 30% 边距
    avail = size - 2 * margin
    s = avail / (2 * half)

    def to_xy(x: float, y: float):
        px = margin + (x - (cx - half)) * s
        py = margin + ((cy + half) - y) * s  # 数学 y 向上，图像 y 向下 → 翻转
        return (px, py)

    return to_xy


def _vector_endpoints(vectors: dict) -> dict:
    """向量的端点集合，用于无图元时的坐标映射。

    4 元素 [x1, y1, x2, y2] 用起终点；2 元素分量 [dx, dy] 视为从原点出发。
    """
    pts = {}
    for name, v in vectors.items():
        if len(v) >= 4:
            pts[f"{name}.s"] = v[0:2]
            pts[f"{name}.e"] = v[2:4]
        elif len(v) == 2:
            pts[f"{name}.e"] = v[0:2]
    return pts


def _side_far_point(side: str, vertex: str, points: dict):
    """角的一边 "AB" 或 "A"：返回远离顶点的那个端点坐标（用于画射线）。"""
    if side in points and side != vertex:
        return points[side]
    if len(side) == 2 and side[0] == vertex and side[1] in points:
        return points[side[1]]
    if len(side) == 2 and side[1] == vertex and side[0] in points:
        return points[side[0]]
    return None


def _radius_px(center: list[float], radius: float, to_xy) -> float:
    """把半径（图内单位）换算成像素：用中心点沿 x 轴偏移 1 单位后的像素距离近似。"""
    x0, y0 = to_xy(center[0], center[1])
    x1, _ = to_xy(center[0] + 1.0, center[1])
    return abs(x1 - x0) * radius


def _draw_arrow(draw, p0, p1, head: int = 10) -> None:
    """画带箭头的线段（绿色），箭头为小三角。"""

    draw.line([p0, p1], fill="green", width=2)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-6:
        return
    ux, uy = dx / length, dy / length
    tip = (p1[0] - ux * head, p1[1] - uy * head)
    # 垂直方向
    vx, vy = -uy, ux
    base1 = (tip[0] + vx * head * 0.5, tip[1] + vy * head * 0.5)
    base2 = (tip[0] - vx * head * 0.5, tip[1] - vy * head * 0.5)
    draw.polygon([p1, base1, base2], fill="green")
