// vision-kit —— DeepSeek Harness（DSH）固定（Host 组合）视觉插件
//
// 给 DSH 的任意文本模型一双"眼睛"：通过 vision_kit 外部视觉模型看图，
// 无需切换多模态模型。与 .opencode/plugins/vision-kit.js（opencode 版）同源，
// 完全复用 vision_kit 的图片预处理（小图放大 / 超大图 2x2 分块）、结构化识别
// （向量/矩阵维度校验 + 采样补漏重试），插件本身只负责路径解析、凭证注入与结果格式化。
//
// 提供两个模型工具：
//   vision_describe        —— 看一张图，中文自然语言描述 / 回答关于图的问题（可传 prompt）
//   vision_extract         —— 结构化识别图中的数字标注（向量/矩阵/坐标/角度/未知量）
//
// 固定安装（随 DSH Web 部署启动自动挂载、重启后保留，无需 cordis_define）：
//   1) 本文件已改写为固定插件模块（CommonJS module.exports + apply(ctx)），
//      工具通过 ctx.tools.register(...) 注册（不再是动态插件的 harness.* 沙箱 API）。
//   2) 在 $DSH_HOME/profiles/web/cordis.patch.yml 插入组合行（name 必须是 file://
//      URL 形式：原始 Windows 路径 D:\... 会被 Node ESM loader 当作 d: scheme 拒绝）：
//        - insert:
//            - id: vision_kit
//              name: 'file:///D:/libs/vision_kit/dsh-plugin/vision-kit.dsh.js'
//   3) 重启 dsh web（或编辑 patch 文件触发热重载）后，三个工具全局可用。
//
// 凭证：从 <vision_kit>/vision.config.json 读取。固定插件运行在真实 Node 进程中，
// 优先读取 process.env.VISION_KIT_DIR；未设置时由本文件自身位置推导仓库根
// （本文件位于 <repo>/dsh-plugin/vision-kit.dsh.js，仓库根即上一级目录），
// 再回退到当前工作目录探测。
//
// 依赖：本机 Python 环境已安装 vision_kit（pip install -e .）且 python 在 PATH 上。

'use strict'

const path = require('node:path')

module.exports = {
  name: 'vision-kit',
  inject: ['subprocess', 'fs', 'tools'],
  apply(ctx) {
    // 视觉模型调用较慢（含补漏重试与超大图分块），放宽到 4 分钟
    const CLI_TIMEOUT_MS = 240000

    // ---- 读取视觉凭证配置（每次调用读取，无缓存）----
    async function loadConfig() {
      const projectDir = await locateKitDir()
      const CONFIG_PATH = path.join(projectDir, 'vision.config.json')
      let configAbs, text
      try {
        configAbs = await ctx.fs.resolve(CONFIG_PATH)
        text = await ctx.fs.readText(configAbs)
      } catch (e) {
        throw new Error(
          '无法读取视觉配置 ' + CONFIG_PATH + '（' + (e && e.message ? e.message : String(e)) + '）。' +
          '请先创建 vision.config.json：复制本仓库根目录的 vision.config.example.json 为 vision.config.json，' +
          '并填入 apiKey。'
        )
      }
      let cfg = {}
      try {
        cfg = JSON.parse(text)
      } catch (e) {
        throw new Error('vision.config.json 不是合法 JSON：' + (e && e.message ? e.message : String(e)))
      }
      if (typeof cfg !== 'object' || cfg === null) throw new Error('vision.config.json 必须是 JSON 对象')
      if (typeof cfg.apiKey !== 'string' || cfg.apiKey.trim() === '') {
        throw new Error('vision.config.json 缺少 apiKey。请编辑 ' + CONFIG_PATH + ' 填入视觉模型 API Key（可在阿里云百炼控制台创建，SK- 开头；模板见 vision.config.example.json）')
      }
      return {
        apiKey: cfg.apiKey.trim(),
        apiBase: typeof cfg.apiBase === 'string' && cfg.apiBase.trim() ? cfg.apiBase.trim() : 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: typeof cfg.model === 'string' && cfg.model.trim() ? cfg.model.trim() : 'qwen3-vl-flash',
        timeout: typeof cfg.timeout === 'number' && cfg.timeout > 0 ? cfg.timeout : 60,
        maxTokens: typeof cfg.maxTokens === 'number' && cfg.maxTokens > 0 ? cfg.maxTokens : 8192,
        python: typeof cfg.python === 'string' && cfg.python.trim() ? cfg.python.trim() : 'python',
        visionKitDir: projectDir,
      }
    }

    // 定位 vision_kit 项目目录（仓库根，含 cli.py / vision.config.json / dsh-plugin/）
    async function locateKitDir() {
      // 1) 显式环境变量（固定插件运行在真实 Node 进程中，可直接读 process.env）
      if (typeof process !== 'undefined' && process.env.VISION_KIT_DIR && process.env.VISION_KIT_DIR.trim()) {
        return path.resolve(process.env.VISION_KIT_DIR.trim())
      }
      // 2) 由本插件自身位置推导仓库根：本文件位于 <repo>/dsh-plugin/vision-kit.dsh.js
      const repoCandidate = path.resolve(__dirname, '..')
      try {
        const probe = await ctx.fs.resolve(path.join(repoCandidate, 'vision.config.json'))
        if (await ctx.fs.stat(probe)) return repoCandidate
      } catch (e) { /* fall through */ }
      // 3) 从当前工作目录相对定位（可直接用相对路径 './'，适合在仓库根启动的场景）
      try {
        const cwd = process.cwd()
        const probe = await ctx.fs.resolve(path.join(cwd, 'vision.config.json'))
        if (await ctx.fs.stat(probe)) return cwd
      } catch (e) { /* fall through */ }
      // 4) 兜底：报错引导
      throw new Error(
        '无法定位 vision_kit 项目目录。请设置环境变量 VISION_KIT_DIR 指向仓库根（含 cli.py 与 vision.config.json 的目录），' +
        '或确认插件文件位于 <仓库根>/dsh-plugin/vision-kit.dsh.js。'
      )
    }

    // ---- 把用户给的图片路径解析为绝对路径 ----
    // 相对路径基于调用方会话工作区（exec.agent.session.header.cwd）；绝对路径原样使用。
    async function resolveImagePath(imagePath, exec) {
      if (typeof imagePath !== 'string' || imagePath.trim() === '') {
        throw new Error('image_path 参数必填（图片路径）')
      }
      const agentCwd = (exec && exec.agent && exec.agent.session && exec.agent.session.header)
        ? exec.agent.session.header.cwd : undefined
      let target
      try {
        target = await ctx.fs.resolve(imagePath.trim(), typeof agentCwd === 'string' && agentCwd ? { cwd: agentCwd } : {})
      } catch (e) {
        throw new Error('无法解析图片路径：' + (e && e.message ? e.message : String(e)))
      }
      const abs = ctx.fs.processPath(target)
      const info = await ctx.fs.stat(target)
      if (!info) throw new Error('图片不存在: ' + abs)
      if (info.type !== 'file') throw new Error('不是普通文件（应为图片文件）: ' + abs)
      return abs
    }

    // ---- 调用 vision_kit CLI，返回解析后的 JSON 结果 ----
    async function runVisionCli(mode, imagePath, prompt, exec, format) {
      const cfg = await loadConfig()
      let pythonExe
      try {
        pythonExe = await ctx.subprocess.resolveExecutable(cfg.python)
      } catch (e) {
        throw new Error('找不到 Python 解释器「' + cfg.python + '」：请在 vision.config.json 的 python 字段指定 python.exe 的绝对路径')
      }
      const argv = [pythonExe, '-m', 'vision_kit.cli', imagePath, '--mode', mode]
      if (prompt && typeof prompt === 'string' && prompt.trim() !== '') argv.push('--prompt', prompt.trim())
      if (format && typeof format === 'string' && format.trim() !== '') argv.push('--format', format.trim())
      // 默认以本项目目录为工作目录（无需 pip 安装，仓库根映射为包根）：
      // 由 loadConfig() 解析出的 visionKitDir 决定（见 locateKitDir）
      const cliDir = typeof cfg.visionKitDir === 'string' && cfg.visionKitDir.trim()
        ? cfg.visionKitDir.trim() : undefined
      const handle = ctx.subprocess.spawn({
        argv,
        cwd: cliDir,
        stdio: {
          stdin: 'ignore',
          stdout: { maxBytes: 1024 * 1024, spill: { maxBytes: 4 * 1024 * 1024 } },
          stderr: { maxBytes: 256 * 1024 },
        },
        graceMs: 5000,
        signal: exec.signal,
        env: {
          VISION_API_KEY: cfg.apiKey,
          VISION_API_BASE: cfg.apiBase,
          VISION_MODEL: cfg.model,
          VISION_TIMEOUT: String(cfg.timeout),
          VISION_MAX_TOKENS: String(cfg.maxTokens),
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
        },
      })
      const outcome = await handle.done
      const reader = handle.collected && handle.collected.stdout
      const errReader = handle.collected && handle.collected.stderr
      const outText = reader ? reader.readFrom(0).text : ''
      const errText = errReader ? errReader.readFrom(0).text : ''
      const lines = outText.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
      let payload = null
      const last = lines[lines.length - 1] || ''
      if (last) {
        try { payload = JSON.parse(last) } catch (e) { payload = null }
      }
      if (payload === null || typeof payload !== 'object') {
        const detail = (errText || outText).trim().split(/\r?\n/).slice(-3).join(' | ')
        throw new Error('vision_kit 输出解析失败（exit ' + String(outcome.exitCode) + '）：' + (detail || '无输出'))
      }
      return payload
    }

    // ---- 工具 1：vision_describe ----
    const disposeDescribe = ctx.tools.register({
      name: 'vision_describe',
      description: '看一张本地图片并返回中文自然语言描述，或回答关于图片内容的具体问题。当任务涉及图片（截图/示意图/题目图/图表/UI 截图/OCR 等）且需要先理解图中内容时使用。由外部视觉模型（vision_kit）驱动，任何文本模型都可获得看图能力。',
      parameters: {
        type: 'object',
        properties: {
          image_path: { type: 'string', description: '图片路径：相对当前工作区的路径（如 test_figure.png）或绝对路径，支持 jpg/png/bmp' },
          prompt: { type: 'string', description: '可选：具体问题或描述要求，缺省使用通用描述提示词' },
        },
        required: ['image_path'],
      },
      output: {
        schema: {
          type: 'object',
          properties: {
            ok: { type: 'boolean', description: '是否成功' },
            text: { type: 'string', description: '视觉模型返回的描述/回答文本' },
            error: { type: 'string', description: '失败原因（ok 为 false 时存在）' },
          },
          additionalProperties: false,
        },
        render(_args, value) {
          if (value && value.ok) return [{ type: 'text', text: String(value.text || '（模型未返回内容）') }]
          return [{ type: 'text', text: '视觉识别失败：' + String((value && value.error) || '未知错误') }]
        },
      },
      timeoutMs: CLI_TIMEOUT_MS,
      isConcurrencySafe() { return true },
      async execute(args, exec) {
        try {
          const abs = await resolveImagePath(args.image_path, exec)
          const payload = await runVisionCli('describe', abs, args.prompt, exec)
          if (payload && payload.ok) return { ok: true, text: String(payload.text || '') }
          return { ok: false, error: String((payload && payload.error) || '识别失败') }
        } catch (e) {
          return { ok: false, error: e && e.message ? e.message : String(e) }
        }
      },
    })

    // ---- 工具 2：vision_extract ----
    const disposeExtract = ctx.tools.register({
      name: 'vision_extract',
      description: '结构化识别图片中的数字标注：向量、矩阵、坐标、线段长度、角度、未知量等（用于题目示意图、几何图、电路图等）。自动做同组向量/矩阵的维度一致性校验并补漏重试，并在维度校验之上做几何自洽校验（三角内角和、向量加法关系、矩阵乘法维度、负值等），返回图形类型 type、vectors、matrices、note、geo_checks（几何校验结论列表）与渲染文本 text。需要精确读出图内数值、或需要判断图内读数是否自洽时使用。',
      parameters: {
        type: 'object',
        properties: {
          image_path: { type: 'string', description: '图片路径：相对当前工作区的路径或绝对路径，支持 jpg/png/bmp' },
        },
        required: ['image_path'],
      },
      output: {
        schema: {
          type: 'object',
          properties: {
            ok: { type: 'boolean', description: '是否成功' },
            error: { type: 'string', description: '失败原因（ok 为 false 时存在）' },
            result: { description: '结构化结果：{type, vectors, matrices, note, geo_checks, diagnostics, text}' },
          },
          additionalProperties: false,
        },
        render(_args, value) {
          if (value && value.ok && value.result) {
            const r = value.result
            const lines = []
            if (r.type) lines.push('图形类型：' + String(r.type))
            if (r.matrices && typeof r.matrices === 'object') {
              for (const k of Object.keys(r.matrices)) lines.push(k + '=' + JSON.stringify(r.matrices[k]))
            }
            if (r.vectors && typeof r.vectors === 'object') {
              for (const k of Object.keys(r.vectors)) lines.push(k + '=' + JSON.stringify(r.vectors[k]))
            }
            if (r.note) lines.push(String(r.note))
            if (r.diagnostics && typeof r.diagnostics === 'object') {
              const d = r.diagnostics
              const marks = { high: '高', medium: '中', low: '低' }
              lines.push('置信度：' + String(marks[d.confidence] || d.confidence || '?') + '（' + String(d.error_code || 'OK') + '，调用 ' + String(d.attempts || 1) + ' 次' + (d.retried ? '，含补漏重试' : '') + '）')
            }
            if (Array.isArray(r.geo_checks) && r.geo_checks.length) {
              lines.push('几何自洽校验：')
              for (const c of r.geo_checks) {
                const mark = c && c.passed ? '  ✓ 通过' : '  ✗ 不通过'
                lines.push(mark + ' [' + String((c && c.rule) || '') + '] ' + String((c && c.detail) || ''))
              }
            }
            return [{ type: 'text', text: lines.join('\n') || '（未识别到数字标注）' }]
          }
          return [{ type: 'text', text: '结构化识别失败：' + String((value && value.error) || '未知错误') }]
        },
      },
      timeoutMs: CLI_TIMEOUT_MS,
      isConcurrencySafe() { return true },
      async execute(args, exec) {
        try {
          const abs = await resolveImagePath(args.image_path, exec)
          const payload = await runVisionCli('extract', abs, undefined, exec)
          if (payload && payload.ok && payload.result) {
            const r = payload.result
            return {
              ok: true,
              result: {
                type: typeof r.type === 'string' ? r.type : '',
                vectors: r.vectors || {},
                matrices: r.matrices || {},
                note: typeof r.note === 'string' ? r.note : '',
                geo_checks: Array.isArray(r.geo_checks) ? r.geo_checks : [],
                diagnostics: (r.diagnostics && typeof r.diagnostics === 'object') ? r.diagnostics : null,
                text: typeof r.text === 'string' ? r.text : '',
              },
            }
          }
          return { ok: false, error: String((payload && payload.error) || '识别失败') }
        } catch (e) {
          return { ok: false, error: e && e.message ? e.message : String(e) }
        }
      },
    })

    return () => { disposeDescribe(); disposeExtract() }
  },
}
