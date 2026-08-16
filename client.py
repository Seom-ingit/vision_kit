"""VisionClient：OpenAI 兼容视觉模型的结构化识别客户端。

用法：
    from vision_kit import VisionClient

    client = VisionClient(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
                          api_key="你的Key", model="qwen3-vl-flash")

    text = client.describe("题目图.png")            # 渲染文本（供 LLM 注入）
    data = client.describe_structured("题目图.png") # 结构化 dict（向量/矩阵/图元/说明）
    client.render_image("题目图.png")               # round-trip：把抽取结果还原成 PNG
"""

import hashlib
import logging
import os
from collections import OrderedDict

from openai import OpenAI

from . import engine

logger = logging.getLogger(__name__)


class VisionClient:
    """OpenAI 兼容视觉模型客户端，带放大预处理 / 矩阵向量结构化 / 维度校验 / 分块。"""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        max_tokens: int = engine.MAX_TOKENS,
        cache: bool = True,
        cache_max: int = 64,
    ):
        if not api_key or not api_base or not model:
            raise ValueError("api_base / api_key / model 均为必填")
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self._client = OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)
        # 内容寻址缓存：以图片文件内容哈希为键，图片变了自动失效（miss）。
        # 只缓存成功结果，失败/None/空串不缓存，下次调用重新走模型。
        self._cache: OrderedDict[str, object] = OrderedDict()
        self._cache_enabled = cache
        self._cache_max = cache_max

    # ---------- 公开接口 ----------

    def describe(self, image_path: str) -> str:
        """返回结构化渲染文本（供 LLM 注入）；识别失败返回空串。

        注意：这是结构化识别结果的渲染文本（向量/矩阵/说明），不是自然语言描述；
        需要自然语言描述请用 describe_natural()。
        """
        data = self.describe_structured(image_path)
        return data["text"] if data else ""

    def describe_natural(self, image_path: str, prompt: str | None = None) -> str:
        """自然语言描述：用通用提示词调用模型，直接返回原始文本（不做结构化解析）。

        整图描述失败（空结果）且确为超大图时，自动分块逐区域描述后拼接（A3），
        避免长截图/大图在单次整图调用下丢失内容。
        """
        base = prompt or engine.GENERAL_PROMPT
        cached = self._cache_get(image_path, "natural", base)
        if cached is not None:
            return cached
        try:
            text = self._caller(image_path, base, engine.TEMP_MAIN, False, self.max_tokens).strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("视觉描述失败: %s", e)
            return ""
        if not text and engine.needs_tiling(image_path):
            # 超大图整图描述为空 → 分块逐区域描述兜底
            try:
                text = engine.describe_tiled(image_path, base, self._caller, self.max_tokens)
            except Exception as e:  # noqa: BLE001
                logger.warning("分块描述失败: %s", e)
                return ""
        if text:  # 只缓存非空结果；失败/空串不缓存（下次重试）
            self._cache_put(image_path, "natural", base, text)
        return text

    def describe_structured(self, image_path: str, prompt: str | None = None) -> dict | None:
        """结构化识别。返回 dict 或 None（模型不可用/全部失败）。

        dict 结构：
            {"type": str, "vectors": {名: [数字]}, "matrices": {名: [[数字]]},
             "note": str, "text": str, "raw": str}
        """
        base = prompt or engine.STRUCTURED_PROMPT
        cached = self._cache_get(image_path, "structured", base)
        if cached is not None:
            return cached
        try:
            # 整图优先：紧凑数学图（即使较宽）不切分，避免破坏向量/矩阵结构
            info = engine.recognize(image_path, base, self._caller, self.max_tokens)
        except Exception as e:  # noqa: BLE001
            logger.warning("视觉识别失败: %s", e)
            return None
        if info and (info.has_content or info.render()):
            data = info.to_dict()
            self._cache_put(image_path, "structured", base, data)
            return data
        # 整图识别失败（无任何内容）→ 若确为超大图，分块兜底（合并后再校验）
        if engine.needs_tiling(image_path):
            try:
                info = engine.recognize_tiled(image_path, base, self._caller, self.max_tokens)
            except Exception as e:  # noqa: BLE001
                logger.warning("分块识别失败: %s", e)
                return None
            if info is not None and info.has_content:
                data = info.to_dict()
                self._cache_put(image_path, "structured", base, data)
                return data
        return None

    def render_image(
        self, image_path: str, out_path: str | None = None, size: int = 800
    ) -> str | None:
        """结构化识别后把抽取结果还原成 PNG（1.4 round-trip 验证）。

        有图元（点/线段/角/圆）时画几何图；纯向量/矩阵图按向量箭头画；
        否则把渲染文本画成图。返回写入的 PNG 路径；识别失败返回 None。
        默认输出到 <图片名>.rendered.png。
        """
        data = self.describe_structured(image_path)
        if not data:
            return None
        from .figure import FigureData  # noqa: PLC0415

        info = FigureData(data.get("raw", ""))
        if not (info.has_content or info.render()):
            return None
        out = out_path or (os.path.splitext(image_path)[0] + ".rendered.png")
        return info.render_image(out, size=size)

    def clear_cache(self) -> None:
        """清空内容寻址缓存（图片内容变化会自动失效，通常无需手动调用）。"""
        self._cache.clear()

    def describe_stats(self, image_path: str) -> dict | None:
        """统计图 → 数据表（4.3）：柱状/折线/饼图 的类别与数值序列（带自洽校验）。

        返回 dict（StatsData 结构）或 None（模型不可用/全部失败）：
            {"type", "categories", "series", "note", "checks", "text", "raw"}
        """
        from . import stats  # noqa: PLC0415

        cached = self._cache_get(image_path, "stats", stats.STATS_PROMPT)
        if cached is not None:
            return cached
        try:
            info = stats.recognize_stats(image_path, self._caller, self.max_tokens)
        except Exception as e:  # noqa: BLE001
            logger.warning("统计图识别失败: %s", e)
            return None
        if info is None:
            return None
        data = info.to_dict()
        self._cache_put(image_path, "stats", stats.STATS_PROMPT, data)
        return data

    # ---------- 内部 ----------

    def _cache_key(self, image_path: str, mode: str, prompt: str) -> str | None:
        """内容寻址键：<mode>:<图片内容 sha256>:<prompt>。

        图片文件读取失败（不存在/权限）时返回 None，上层照常走原逻辑
        （异常由现有调用方处理，这里不抛）。
        """
        try:
            with open(image_path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None
        return f"{mode}:{digest}:{prompt}"

    def _cache_get(self, image_path: str, mode: str, prompt: str) -> object | None:
        """查缓存：命中返回缓存值（None 表示未命中/缓存关闭/读文件失败）。"""
        if not self._cache_enabled:
            return None
        key = self._cache_key(image_path, mode, prompt)
        if key is None:
            return None
        value = self._cache.get(key)
        if value is not None:
            self._cache.move_to_end(key)  # LRU 刷新
            return value
        return None

    def _cache_put(self, image_path: str, mode: str, prompt: str, value: object) -> None:
        """写入缓存（仅成功结果，None 不缓存）；容量超限时淘汰最旧一条。"""
        if not self._cache_enabled or value is None:
            return
        key = self._cache_key(image_path, mode, prompt)
        if key is None:
            return
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    def _caller(
        self, image_path: str, prompt: str, temperature: float, do_sample: bool, max_tokens: int
    ) -> str:
        b64 = engine.preprocess(image_path)
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ],
            "max_tokens": max_tokens,
            # 统一用 temperature 控制：0.0 贪心（确定性），>0 采样重试。
            # 不再使用智谱私有的 extra_body={"do_sample": ...}，保证 DashScope 等
            # 其他 OpenAI 兼容端点不因未知参数报错。
            "temperature": temperature,
        }
        # do_sample 参数保留以兼容调用方签名（engine 传入 False/True 区分贪心/采样）
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
