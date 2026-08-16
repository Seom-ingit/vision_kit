"""vision_kit：OpenAI 兼容视觉模型的结构化识别工具包。

能力：
- 向量/矩阵/图形结构化识别（数字维度一致性校验，防漏行漏元素）
- 小图自动放大 + 超大图自动 2x2 分块
- 贪心主调用（确定性）+ 采样补漏重试

用法：
    from vision_kit import VisionClient

    client = VisionClient(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
                          api_key="你的Key", model="qwen3-vl-flash")

    text = client.describe("题目图.png")            # 渲染文本（供 LLM 注入）
    data = client.describe_structured("题目图.png") # 结构化 dict
"""

from .client import VisionClient

__all__ = ["VisionClient"]
__version__ = "0.3.0"
