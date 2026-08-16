"""vision_kit.cli — 命令行入口，供 opencode 视觉插件 / 外部脚本直接驱动 vision_kit。

用法:
    python -m vision_kit.cli <image_path> [--mode describe|extract|both|render|stats] [--prompt TEXT]

配置（环境变量，与 mcp_server 一致）:
    VISION_API_KEY     必填。OpenAI 兼容视觉服务的 API Key
    VISION_API_BASE    可选。API 地址，默认 https://dashscope.aliyuncs.com/compatible-mode/v1
    VISION_MODEL       可选。视觉模型名，默认 qwen3-vl-flash
    VISION_TIMEOUT     可选。请求超时秒数，默认 60
    VISION_MAX_TOKENS  可选。输出 token 上限，默认 8192（qwen3-vl-flash 上限）

输出:
    stdout 输出一行 JSON：
        {"ok": true,  "mode": "describe", "text": "..."}
        {"ok": true,  "mode": "extract",  "result": {"type": ..., "vectors": ..., "matrices": ..., "note": ..., "text": ..., "raw": ...}}
        {"ok": true,  "mode": "both",     "result": {"describe": "...", "structured": {...}}}
        --render 时 extract/both 的 result 额外带 "rendered": "输出PNG路径"
    失败时退出码 1：
        {"ok": false, "error": "..."}
"""

import argparse
import json
import logging
import os
import sys

from . import engine
from .client import VisionClient

logger = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen3-vl-flash"
_DEFAULT_TIMEOUT = 60.0


def _client() -> VisionClient:
    api_key = os.environ.get("VISION_API_KEY")
    if not api_key:
        raise RuntimeError(
            "缺少环境变量 VISION_API_KEY（可在 opencode.json 的 vision-kit MCP env 中配置，"
            "或导出到 shell 环境）"
        )
    return VisionClient(
        api_base=os.environ.get("VISION_API_BASE", _DEFAULT_API_BASE),
        api_key=api_key,
        model=os.environ.get("VISION_MODEL", _DEFAULT_MODEL),
        timeout=float(os.environ.get("VISION_TIMEOUT", _DEFAULT_TIMEOUT)),
        max_tokens=int(os.environ.get("VISION_MAX_TOKENS", engine.MAX_TOKENS)),
    )


def _fail(message: str) -> int:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return 1


def _to_tikz(raw: str) -> str | None:
    """把结构化原始输出转成 TikZ 片段；无可绘制内容返回 None。"""
    from .figure import FigureData  # noqa: PLC0415

    info = FigureData(raw)
    try:
        return info.to_tikz()
    except Exception as e:  # noqa: BLE001
        logger.warning("TikZ 转换失败: %s", e)
        return None


def _template(figure_type: str) -> str:
    """按图型取场景化提示词模板（4.1）；未知类型回退通用结构化提示词。"""
    from .prompts import PROMPT_TEMPLATES  # noqa: PLC0415

    return PROMPT_TEMPLATES.get(figure_type, engine.STRUCTURED_PROMPT)


def _run_stats(client: VisionClient, args) -> int:
    """统计图 → 数据表（4.3），按 --format json|netlist|tree 无关，固定输出完整 result。"""
    from .stats import recognize_stats  # noqa: PLC0415

    info = recognize_stats(args.image, client._caller, client.max_tokens)
    if info is None:
        return _fail("统计识别失败：模型未返回有效内容。")
    print(
        json.dumps({"ok": True, "mode": "stats", "result": info.to_dict()}, ensure_ascii=False)
    )
    return 0


def _render_result(client: VisionClient, image: str, structured: dict) -> str | None:
    """把结构化结果还原成 PNG（round-trip），输出到 <图片名>.rendered.png。"""
    from .figure import FigureData  # noqa: PLC0415

    info = FigureData(structured.get("raw", ""))
    if not (info.has_content or info.render()):
        return None
    out = os.path.splitext(image)[0] + ".rendered.png"
    try:
        return info.render_image(out)
    except Exception as e:  # noqa: BLE001
        logger.warning("渲染回写失败: %s", e)
        return None


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，模型返回常含 Unicode 字符，统一以 UTF-8 输出
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        prog="vision-kit.cli",
        description="OpenAI 兼容视觉模型的结构化识别命令行入口",
    )
    parser.add_argument(
        "image", help="本地图片路径（jpg/png/bmp 等，小图自动放大，超大图自动分块）"
    )
    parser.add_argument(
        "--mode",
        choices=("describe", "extract", "both", "render", "stats"),
        default="describe",
        help="describe=自然语言描述/问答，extract=结构化识别（向量/矩阵/图元），both=两者都要，"
        "render=结构化识别并还原成 PNG，stats=统计图转数据表",
    )
    parser.add_argument(
        "--type",
        choices=("default", "geometry", "function", "vector", "statistics"),
        default="default",
        help="extract/both 的图型专用提示词模板（4.1）：geometry/function/vector/statistics，"
        "缺省 default 用通用结构化提示词",
    )
    parser.add_argument("--prompt", default=None, help="可选的自定义提示词 / 问题")
    parser.add_argument(
        "--tikz",
        action="store_true",
        help="extract/both 时把抽取结果转成可编译 TikZ 片段（LaTeX），输出到 result.tikz",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="结构化识别后把抽取结果还原成 PNG（round-trip），输出到 <图片名>.rendered.png",
    )
    parser.add_argument("--verbose", action="store_true", help="把日志输出到 stderr")
    args = parser.parse_args(argv)

    if args.mode == "render":
        args.mode, args.render = "extract", True

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not os.path.isfile(args.image):
        return _fail(f"图片文件不存在: {args.image}")

    try:
        client = _client()
    except Exception as e:  # noqa: BLE001
        return _fail(f"客户端初始化失败: {e}")

    try:
        if args.mode == "stats":
            return _run_stats(client, args)
        if args.mode in ("describe", "both"):
            text = client.describe_natural(args.image, args.prompt or engine.GENERAL_PROMPT)
            if args.mode == "describe":
                if not text:
                    return _fail("识别失败：模型未返回有效内容。")
                print(
                    json.dumps({"ok": True, "mode": "describe", "text": text}, ensure_ascii=False)
                )
                return 0
        if args.mode in ("extract", "both"):
            # extract 模式只做结构化识别；自定义 prompt 优先，否则按 --type 取场景化模板
            structured = client.describe_structured(args.image, args.prompt or _template(args.type))
            if not structured:
                return _fail("识别失败：模型未返回有效内容。")
            if args.tikz:
                structured["tikz"] = _to_tikz(structured.get("raw", ""))
            if args.render:
                out = _render_result(client, args.image, structured)
                if out:
                    structured["rendered"] = out
            if args.mode == "extract":
                print(
                    json.dumps(
                        {"ok": True, "mode": "extract", "result": structured}, ensure_ascii=False
                    )
                )
                return 0
        # both
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "both",
                    "result": {"describe": text, "structured": structured},
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("识别失败")
        return _fail(f"识别失败: {e}")


if __name__ == "__main__":
    sys.exit(main())
