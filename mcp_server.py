"""opencode MCP 服务器：将 vision_kit 的视觉模型能力暴露为 MCP 工具（本地 stdio）。

环境变量：
    VISION_API_KEY     必填。OpenAI 兼容视觉服务的 API Key
    VISION_API_BASE    可选。API 地址，默认 https://dashscope.aliyuncs.com/compatible-mode/v1
    VISION_MODEL       可选。视觉模型名，默认 qwen3-vl-flash
    VISION_TIMEOUT     可选。请求超时秒数，默认 60
    VISION_MAX_TOKENS  可选。输出 token 上限，默认 8192（qwen3-vl-flash 上限）
"""

import logging
import os

from mcp.server.fastmcp import FastMCP

from . import engine
from .client import VisionClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen3-vl-flash"
_DEFAULT_TIMEOUT = 60.0

mcp = FastMCP(
    "vision-kit",
    instructions=(
        "使用视觉模型分析图片。提供三个工具："
        "describe_image（通用图文描述）、describe_image_structured"
        "（结构化识别图中的向量/矩阵/标注）、describe_image_stats"
        "（统计图转数据表：类别/系列数值）。均需传入图片的本地文件路径。"
    ),
)


def _client() -> VisionClient:
    api_key = os.environ.get("VISION_API_KEY")
    if not api_key:
        raise RuntimeError("缺少环境变量 VISION_API_KEY")
    return VisionClient(
        api_base=os.environ.get("VISION_API_BASE", _DEFAULT_API_BASE),
        api_key=api_key,
        model=os.environ.get("VISION_MODEL", _DEFAULT_MODEL),
        timeout=float(os.environ.get("VISION_TIMEOUT", _DEFAULT_TIMEOUT)),
        max_tokens=int(os.environ.get("VISION_MAX_TOKENS", engine.MAX_TOKENS)),
    )


@mcp.tool()
def describe_image(image_path: str, prompt: str | None = None) -> str:
    """通用视觉描述：调用视觉模型观察图片并返回中文描述文本。

    Args:
        image_path: 本地图片文件路径（支持 jpg/png/bmp 等，超长图自动分块）。
        prompt: 可选的自定义描述要求；缺省使用通用描述提示词。
    """
    client = _client()
    text = client.describe_natural(image_path, prompt or engine.GENERAL_PROMPT)
    if not text:
        return "识别失败：模型未返回有效内容。"
    return text


@mcp.tool()
def describe_image_structured(image_path: str) -> dict:
    """结构化识别：提取图片中带数字的标注（向量/矩阵/坐标/角度/未知量）。

    Args:
        image_path: 本地图片文件路径。

    Returns:
        dict，包含 type（图形类型）、vectors（向量）、matrices（矩阵）、
        note（说明）与 text（渲染文本）。识别失败时返回
        {"error": "..."}（避免返回 None 与声明类型 dict 不符）。
    """
    client = _client()
    result = client.describe_structured(image_path)
    if result is None:
        return {
            "error": "识别失败：模型未返回有效内容。",
            "type": "",
            "vectors": {},
            "matrices": {},
            "note": "",
            "text": "",
        }
    return result


@mcp.tool()
def describe_image_stats(image_path: str) -> dict:
    """统计图 → 数据表：提取柱状/折线/饼图中的类别与数值序列（4.3）。

    Args:
        image_path: 本地图片文件路径。

    Returns:
        dict，包含 type（图表类型）、categories（类别）、series（系列数值）、
        note（说明）与 checks（自洽校验：长度对齐 / 百分比求和 / 非负）。
        识别失败时返回 {"error": "..."}。
    """
    from .stats import recognize_stats  # noqa: PLC0415

    client = _client()
    try:
        info = recognize_stats(image_path, client._caller, client.max_tokens)
    except Exception as e:  # noqa: BLE001
        logger.warning("统计图识别失败: %s", e)
        return {"error": "统计图识别失败。"}
    if info is None:
        return {
            "error": "统计识别失败：模型未返回有效内容。",
            "type": "",
            "categories": [],
            "series": [],
            "note": "",
            "checks": [],
            "text": "",
        }
    return info.to_dict()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
