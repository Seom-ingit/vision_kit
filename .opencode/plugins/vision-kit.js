// vision-kit opencode 插件：给 coding agent 一双“眼睛”。
//
// 基于 vision_kit 项目（D:\libs\vision_kit）：
//   复用其图片预处理（小图放大 / 超大图 2x2 分块）、结构化识别
//   （向量/矩阵维度校验 + 采样补漏重试），通过 `python -m vision_kit.cli`
//   调用，不重复实现任何视觉逻辑。
//
// 安装：
//   - 项目级：放在 <project>/.opencode/plugins/ 下（本文件即此位置），
//     在该项目里运行 opencode 时自动加载；
//   - 全局：复制到 ~/.config/opencode/plugins/，所有项目生效。
//
// 凭证（按优先级）：
//   1. 环境变量 VISION_API_KEY / VISION_API_BASE / VISION_MODEL / ...
//   2. opencode 配置中 mcp["vision-kit"].env（全局 ~/.config/opencode/opencode.json
//      或项目 .opencode/opencode.json）
//
// 提供两个工具：
//   vision_describe        —— 看一张图，自然语言描述 / 回答关于图的问题
//   vision_extract         —— 结构化识别图中的数字标注（向量/矩阵/坐标/角度等）

import { tool } from "@opencode-ai/plugin";
import { execFile } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join, delimiter } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const PYTHON = process.env.VISION_PYTHON || "python";
// 可选：vision_kit 项目目录。设置后 CLI 以该目录为工作目录并把其加入 PYTHONPATH，
// 未执行 pip install 也能 `python -m vision_kit.cli`；不设置则依赖已安装的 vision_kit。
const VISION_KIT_DIR = process.env.VISION_KIT_DIR || "";
const CLI_CALL_TIMEOUT_MS = 240_000; // 视觉模型较慢，含重试/分块，放宽到 4 分钟

/** 读取一个 opencode.json 里 mcp["vision-kit"] 的 env / environment 块 */
function readMcpVisionEnv(file) {
  try {
    const raw = JSON.parse(readFileSync(file, "utf8"));
    const mcp = raw && raw.mcp ? raw.mcp["vision-kit"] : undefined;
    if (!mcp) return {};
    return { ...(mcp.env || {}), ...(mcp.environment || {}) };
  } catch {
    return {};
  }
}

/** 解析视觉凭证：shell 环境变量 > 项目配置 > 全局配置 */
function loadVisionEnv(directory) {
  const sources = [
    {},
    readMcpVisionEnv(join(homedir(), ".config", "opencode", "opencode.json")),
    readMcpVisionEnv(join(directory, ".opencode", "opencode.json")),
  ];
  const env = Object.assign({}, ...sources);
  const names = [
    "VISION_API_KEY",
    "VISION_API_BASE",
    "VISION_MODEL",
    "VISION_TIMEOUT",
    "VISION_MAX_TOKENS",
    "VISION_PYTHON",
  ];
  for (const n of names) if (process.env[n]) env[n] = process.env[n];
  return env;
}

/** 把用户给的图片路径解析为绝对路径（相对路径基于会话目录） */
function resolveImagePath(image, directory) {
  if (!image || typeof image !== "string") throw new Error("image 参数必填（图片路径）");
  const p = isAbsolute(image) ? image : join(directory, image);
  if (!existsSync(p)) throw new Error(`图片不存在: ${p}（可传相对路径或绝对路径）`);
  return p;
}

/** 调用 vision_kit CLI，返回解析后的 JSON 结果 */
async function callVisionCli(mode, imagePath, prompt, directory, signal, format) {
  const env = loadVisionEnv(directory);
  if (!env.VISION_API_KEY) {
    throw new Error(
      "缺少视觉凭证 VISION_API_KEY：请在 opencode.json 的 mcp[\"vision-kit\"].env 中配置，" +
        "或导出环境变量（参考 vision_kit/opencode.example.json）"
    );
  }
  const args = ["-m", "vision_kit.cli", imagePath, "--mode", mode];
  if (prompt) args.push("--prompt", prompt);
  if (format) args.push("--format", format);
  const runEnv = { ...process.env, ...env };
  // 未 pip 安装时也能从源码目录加载 vision_kit
  if (VISION_KIT_DIR) {
    runEnv.PYTHONPATH = [VISION_KIT_DIR, runEnv.PYTHONPATH].filter(Boolean).join(delimiter);
  }
  // 强制子进程 UTF-8 输出，避免 Windows 控制台 GBK 导致中文 JSON 乱码
  runEnv.PYTHONIOENCODING = "utf-8";
  runEnv.PYTHONUTF8 = "1";
  try {
    const { stdout, stderr } = await execFileAsync(PYTHON, args, {
      env: runEnv,
      cwd: VISION_KIT_DIR || directory,
      timeout: CLI_CALL_TIMEOUT_MS,
      maxBuffer: 32 * 1024 * 1024,
      signal,
      windowsHide: true,
    });
    const line = stdout.trim().split("\n").filter(Boolean).at(-1) || "{}";
    const res = JSON.parse(line);
    if (!res.ok) throw new Error(res.error || "vision_kit 返回失败");
    return res;
  } catch (err) {
    if (err && err.name === "AbortError") throw new Error("视觉识别已取消");
    const tail = String(err && err.stderr ? err.stderr : err && err.message ? err.message : err)
      .trim()
      .split("\n")
      .slice(-5)
      .join(" | ");
    throw new Error(`视觉识别调用失败: ${tail}`);
  }
}

export const VisionKitPlugin = async (ctx) => {
  return {
    tool: {
      vision_describe: tool({
        description:
          "看一张图片并返回中文自然语言描述，或回答关于图片内容的具体问题。当任务涉及图片（截图/示意图/题目图/图表/UI 截图/OCR 等）且需要先理解图中内容时使用。",
        args: {
          image: tool.schema
            .string()
            .describe("图片路径：相对当前目录（如 assets/a.png）或绝对路径，支持 jpg/png/bmp"),
          prompt: tool.schema
            .string()
            .optional()
            .describe("可选：具体问题或描述要求，缺省为通用描述"),
        },
        async execute(args, context) {
          const p = resolveImagePath(args.image, context.directory);
          const res = await callVisionCli("describe", p, args.prompt, context.directory, context.abort);
          return {
            title: `看图：${args.image}`,
            output: String(res.text || "").trim() || "（模型未返回内容）",
          };
        },
      }),

      vision_extract: tool({
        description:
          "结构化识别图片中的数字标注：向量、矩阵、坐标、线段长度、角度、未知量等。返回图形类型、vectors（向量）、matrices（矩阵）与 note（说明），自动做维度一致性校验并补漏重试。需要精确读出图里的数值（如数学题图、电路图、几何图）时使用。",
        args: {
          image: tool.schema
            .string()
            .describe("图片路径：相对当前目录或绝对路径，支持 jpg/png/bmp"),
        },
        async execute(args, context) {
          const p = resolveImagePath(args.image, context.directory);
          const res = await callVisionCli("extract", p, undefined, context.directory, context.abort);
          const r = res.result || {};
          return {
            title: `结构化识别：${args.image}`,
            output: String(r.text || "").trim() || "（未识别到数字标注）",
            metadata: {
              type: r.type || "",
              vectors: r.vectors || {},
              matrices: r.matrices || {},
              note: r.note || "",
              geo_checks: r.geo_checks || [],
              diagnostics: r.diagnostics || null,
              raw: r.raw || "",
            },
          };
        },
      }),
    },
  };
};

export default VisionKitPlugin;
