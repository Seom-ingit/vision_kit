**[English](./README.en.md)** · 简体中文

<div align="center">

# 👁️ vision_kit

**Make your AI agent a math tutor.** · 把 AI 变成能教你做题的家教。

看数学 / 几何题图，靠的不是看图，而是「看对」：从图里抽取向量、矩阵、坐标、角度，
自动检查维度是否一致、几何是否自洽，漏了重试补全、大图自动分块。
结果不对就读不出来——而不是把错题当真。任意文本模型，接上它就能"看见"数学图。

`Python` · `OpenAI 兼容视觉模型` · `MCP` · `DeepSeek Harness` · `opencode`

</div>

---

**它是唯一会「算」的视觉插件：** 竞争的插件都只会「看」（通用看图问答），
vision_kit 在 VLM 输出之后加上一层**确定性校验**——
`三角形内角和 ≠ 180°`、`向量加法不匹配`、`矩阵维度不符`、`负值/越界`，
任一触发就采样重试，重试耗尽后把 `✓ / ✗` 逐条返回给 agent，让它能发现
「VLM 读错数」而不是把错题当真。

![对抗演示：VLM 自信地错 vs vision_kit 拦截](docs/images/demo_catch.png)

> 对抗样本：图上标注 ∠A=60° ∠B=60° ∠C=70°（内角和 190°）。纯 VLM 直接采信并输出；vision_kit 的校验层发现内角和 ≠ 180°，返回 `✗ GEOMETRY_INCONSISTENT` 并定向重试——**错误成为显式信号**。

---

## 能力一览

![四段式闭环：读图 → 校验 → 重试 → 诊断](docs/images/flow.png)

| 能力 | 说明 |
| --- | --- |
| **结构化识别** | 提取向量 / 矩阵 / 坐标 / 角度 / 未知量等数字标注 |
| **图元 DOM** | 点 / 线段 / 角 / 圆 / 四边形 类型化图元（`figures`），引用完整性校验（端点/顶点/圆心/四边形顶点必须已定义） |
| **维度一致性** | 同组向量 / 矩阵自动校验维度，发现漏数自动采样重试补齐 |
| **几何自洽校验** | 三角内角和、向量加法、矩阵乘法维度、线段长度 vs 端点距离、三角形不等式、勾股定理、圆定理（圆上点到圆心距离=半径、直径对直角）、相似三角形、平行四边形对边相等、函数图对称轴、负值/值域 —— 纯逻辑，零外部依赖 |
| **定向补漏重试** | 校验失败的具体规则（维度/角度和/向量加法/边长…）回流成**定向重试提示**，第 2/3 次采样重试带着「错在哪」去读图 |
| **置信度 / 诊断** | 每条结果带 `diagnostics`：错误码（DIMENSION_MISMATCH / GEOMETRY_INCONSISTENT / NO_NUMERIC_CONTENT…）+ 整体/逐值置信度 + 重试次数，agent 可据错误码行动 |
| **图像预处理** | 小图自动放大（保证数字清晰）、超大图 2×2 带重叠分块后**合并再校验**（跨块向量/几何关系不丢失）；describe 超大图自动分块描述 |
| **渲染回写** | 抽取结果用 PIL 还原成 PNG（round-trip），供人 / 模型目视比对抓幻觉 |
| **TikZ 输出** | DOM → 可编译 LaTeX/TikZ 片段（`--tikz`），题图可"照着重建"用于排版/复刻 |
| **统计图 → 数据表** | 柱状 / 折线 / 饼图 → 类别 + 系列数值，带长度对齐 / 百分比求和≈100 / 非负校验（`--mode stats`） |
| **基准 + 回归快照** | `benchmark/` 三套基准：12 张合成题图（数值准确率/召回/幻觉/一致性/几何自洽/重试率，`--baseline` 量化校验层增益）+ **6 张对抗注入集**（矛盾标注，校验层防护率）+ **12 张困难集**（扰动图，补漏重试价值）；`tests/snapshots/` 数值级回归，CI 自动比对 |
| **多端接入** | 同一个引擎多端复用：DSH 插件 / opencode 插件 / MCP / CLI |

---

## 🤏 怎么选：describe 还是 extract？

vision_kit 的两个工具**分工不同**，选对才能发挥各自优势：

| 场景 | 推荐工具 | 说明 |
| --- | --- | --- |
| **非数学/几何图**（照片、场景图、流程图、电气/电路示意图、通用图片问答） | `describe` | 只看图返回自然语言描述/回答问题，泛化能力强，不会硬套结构 |
| **带数值标注的数学题图**（向量 / 矩阵 / 点坐标 / 角度 / 长度 / 未知量） | `extract` | 结构化提取 + 维度一致 + 几何自洽校验，是它最擅长的 |
| 想两者都要 | `both` | 先描述全貌，再结构提取 |

**几个需要知道的边界：**
- **`extract` 是"数学图专用"**，它强制按向量/矩阵/坐标输出结构。对**没有这类结构**的图（如电气布局、UI、照片），模型可能为凑格式而**编造不存在的向量**——这种情况请改用 `describe`。
- **`describe` 不看结构**，适合通用看图；但对**带精确数字标注的题图**，它不会做维度/几何校验，拿不到可信的结构化结果。
- 判断一张图"算不算数学题图"：**画面上是否要精确读出成组的数字序列**（向量、矩阵、坐标、角度）。是 → `extract`；否 → `describe`。

> 一张（黑底）电气原理图测试：`describe` 能准确识别三相电源、PLC、各类开关/端子；若用它跑 `extract`，因图中无向量/矩阵标注，可能得到**编造的假向量**——这正是"非数学图请用 describe"的典型场景。

---

## 对比：为什么是 vision_kit

| 插件 / 项目 | 定位 | vision_kit 的差异 |
| --- | --- | --- |
| `dsh-vision` (view_image) | 通用看图问答 | ✅ 唯一做**结构化数学/几何图**的结构输出 |
| `dsh-tool-see-image` (see_image) | 通用看图 | ✅ 有维度校验、几何自洽、补漏重试 |
| `dsh-plugin-deepeye` | 通用看图 | ✅ 有确定性自洽校验，非纯描述 |
| `dsh-vision-toolkit` | OCR / UI 还原 | ✅ 面向数学标注，校验数值自洽 |

> **一句话**：「读图 + 几何自洽校验」的组合目前无人做。vision_kit 把校验放在 **VLM 输出之后**——
> 纯逻辑、可测试、可评测，不依赖特定的视觉模型能力。

---

## 📊 基准数据（12 张合成题图 · `qwen3-vl-flash`）

![开源基准数字卡](docs/images/benchmark.png)

首份真实基准已发布（[`benchmark/REPORT.md`](benchmark/REPORT.md)，可复现）：

| 指标 | 值 |
| --- | --- |
| 数值准确率 | **97.2%**（35/36 分量） |
| 召回 | 10/10 标注名 |
| 幻觉数 | **0** |
| 维度/引用一致性率 | 100% |
| 几何自洽率 | 100% |
| 平均调用次数 | 1.0（无重试，贪心直出即达标） |
| 平均延迟 | 3.1s/图 |

> 唯一未满分项 `tri_sides_01`：顶点 C 坐标（3.879→5.9 级读数偏差）未精确命中 GT，
> 但几何自洽校验全部通过（偏差在容差内）——这正是"不把对的说错"的设计。
> `--baseline` 基线对比显示：这批简单合成图上确定性层是**安全网**（防止漏数/幻觉被当真），
> 而非分数助推器；在更复杂的真实题图（模糊/密集标注）上，补漏重试的收益会显现。

---

## 🤔 第一次用，先确认这几点

**1. 拿视觉模型的 Key（阿里云百炼 / DashScope）**
推荐模型 `qwen3-vl-flash` 来自**阿里云百炼**。去 [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) 注册/登录阿里云 → 开通百炼 → 创建「API Key」（`SK-` 开头）。所有使用方式（CLI / MCP / 插件）都只需要这一个 Key。

**2. 三种接入方式，先跑通哪一个？**
- **只想快点看效果 → 用「命令行 CLI」**（最直接，一条命令出结果）。
- **想让 opencode 的 agent 能看图 → 用「opencode 视觉插件」（推荐给开发）**。
- **想在自己写的 Python 脚本里调用 → 用「快速开始（Python API）」**。

下面按「CLI → opencode 插件 → DSH 插件」顺序介绍，按需跳到你需要的章节即可。

**3. 工作在哪个目录？**
仓库根目录就是 vision_kit 的包根（见 pyproject 的 `package-dir`）。请**在克隆下来的仓库根目录**运行命令与放置图片，
这样 `python -m vision_kit.*` 和 `test_figure.png` 才能被找到。

---

## 快速开始（Python)

> 💡 **推荐视觉模型：`qwen3-vl-flash`** —— 阿里云百炼（DashScope）开箱即用的轻量多模态视觉模型，
> 对中文数学/几何题图里的数字标注识别稳定，速度与成本兼顾。vision_kit 已针对 **OpenAI 兼容协议** 做了参数化调用，
> 直接通过环境变量指定即可，无需改代码。
> 若你更喜欢智谱的 `glm-4v-flash`：把 `VISION_API_BASE` 换成 `https://open.bigmodel.cn/api/paas/v4`、`VISION_MODEL` 换成 `glm-4v-flash`，
> **并记得把 `VISION_MAX_TOKENS` 同步改回 `1024`**（glm-4v-flash 上限是 1024，见下方「参数调优」）。
> 下面示例默认使用 DashScope（阿里云百炼）兼容端点。

```bash
# 克隆后，在仓库根目录执行
pip install -e .            # 安装（openai / pillow / mcp）
export VISION_API_KEY=你的Key    # 填你在阿里云百炼创建的 SK- 开头 Key
# 下面两个是可选默认值（官方 DashScope 端点 + qwen3-vl-flash），不设也能用：
export VISION_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1   # 可选
export VISION_MODEL=qwen3-vl-flash                                        # 可选
```

```python
from vision_kit import VisionClient

client = VisionClient(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
                      api_key="你的Key", model="qwen3-vl-flash")
text = client.describe("题目图.png")            # 渲染文本（供 LLM 注入）
data = client.describe_structured("题目图.png") # 结构化 dict
```

### 需要先安装吗？

vision_kit 把**仓库根目录映射为包根**（`pyproject.toml` 的 `package-dir: vision_kit = "."`）。
有两种使用方式，按需选一种即可：

- **推荐（CLI / MCP 最省事）**：`pip install -e .` 安装一次，之后任意目录都能 `python -m vision_kit.*`。
- **也可以不安装**：把仓库目录加入环境变量 `PYTHONPATH` 后直接 `import vision_kit`（DSH 插件的 `visionKitDir` 就是这么用的）。

**两者二选一即可，不必都做。** 若既没安装、又没设 `PYTHONPATH`，`python -m vision_kit.cli` 会报"找不到模块"。

---

## 命令行（供插件 / 脚本调用）

```bash
python -m vision_kit.cli 题目图.png --mode describe   # 自然语言描述 / 问答
python -m vision_kit.cli 题目图.png --mode extract    # 结构化识别（JSON）
python -m vision_kit.cli 题目图.png --mode both
python -m vision_kit.cli 统计图.png --mode stats      # 统计图 → 数据表（类别/系列数值）
python -m vision_kit.cli 几何图.png --mode extract --type geometry   # 图型专用提示词模板
python -m vision_kit.cli 几何图.png --mode extract --tikz            # 附带可编译 TikZ 片段
# 或安装后：vision-kit-cli 题目图.png --mode extract
```

输出单行 JSON：`{"ok": true, "mode": "extract", "result": {...}}`，失败时 `{"ok": false, "error": "..."}` 且退出码 1。
凭证读取环境变量 `VISION_API_KEY / VISION_API_BASE / VISION_MODEL / VISION_TIMEOUT / VISION_MAX_TOKENS`（与 MCP 服务器一致）。

### 试着跑一下（最快验证）

```bash
# 在仓库根目录（这样能找到自带的 test_figure.png）
python -m vision_kit.cli test_figure.png --mode extract
```

> 注意：`test_figure.png` 是本仓库自带图。**用全局插件 / 在自己项目里跑时，工作区里没有它**，
> 请换成你自己的图片路径，或先 `cd` 到仓库根目录再跑。图片路径按**当前工作目录**解析。

---

## opencode 视觉插件（推荐）

给 coding agent 一双“眼睛”：无需切换多模态模型，任意文本模型也能看图。

### 提供两个工具

| 工具 | 作用 |
| --- | --- |
| `vision_describe` | 看一张图，返回中文描述，或回答关于图的**具体问题**（传入 `prompt`） |
| `vision_extract` | **结构化**识别图中的数字标注：向量 / 矩阵 / 坐标 / 角度，带维度校验与补漏重试 |

### 安装

插件是放在插件目录的 JS 文件，opencode 启动时自动加载（无需改配置）：

```bash
# 前提：先克隆本仓库，并使用仓库根目录的 .opencode/plugins/vision-kit.js

# 项目级（只在该项目生效）：先创建目标插件目录（若不存在）再复制
mkdir -p <你的项目>/.opencode/plugins
cp .opencode/plugins/vision-kit.js <你的项目>/.opencode/plugins/

# 全局（所有项目生效）：
mkdir -p ~/.config/opencode/plugins
cp .opencode/plugins/vision-kit.js ~/.config/opencode/plugins/
```

> 上面的 `cp` 需要在**仓库根目录**执行。若只想要插件、不用整个库，直接拷贝那个单文件即可，
> 插件运行时会调用 `python -m vision_kit.cli`，所以目标机器仍要能 `import vision_kit`（见「要不要 pip install」）。

### 凭证（按优先级）

1. 环境变量 `VISION_API_KEY` / `VISION_API_BASE` / `VISION_MODEL` / …
2. opencode 配置 `mcp["vision-kit"].env`（全局 `~/.config/opencode/opencode.json` 或项目 `.opencode/opencode.json`，即 [opencode.example.json](opencode.example.json) 中的写法）

无需重复填 Key：只要你的 opencode 配置里已有 vision-kit MCP，插件自动复用同一组凭证。

> **到底该在哪儿填 Key？一张表说清（避免到处都填乱）：**

| 你要用 | 该在哪儿配 | 说明 |
| --- | --- | --- |
| opencode **插件** | opencode 配置 `mcp["vision-kit"].env` 的 `VISION_API_KEY` | 插件自动复用，最省事 |
| **CLI / MCP 服务器** | 环境变量 `VISION_API_KEY`（或 opencode.json env） | 运行前 `export` 或写在 env |
| **DSH 插件** | `vision.config.json` 的 `apiKey` | 见下方 DSH 章节 |

三处配同一个 Key 即可，**不必每处都填**；按你实际用的接入方式配对应那一个地方就够。

### 实现方式

插件不重复实现任何视觉逻辑：调用 `python -m vision_kit.cli`，完全复用本项目的图片预处理、结构化识别、维度校验、补漏重试与超大图分块。插件本身只负责路径解析（相对路径基于会话目录）、凭证注入与结果格式化。

### 试一下

```bash
opencode run "用 vision_describe 看一下 test_figure.png，告诉我图里有哪些向量"
opencode run "用 vision_extract 提取 test_figure.png 中的结构化数据"
```

`test_figure.png` 是本仓库自带的一张向量题示例图，**在仓库根目录**。
用全局插件 / 在别的工作区跑时，请换成你自己图片的绝对路径。

---

## opencode MCP 服务器

```bash
python -m vision_kit.mcp_server   # 或安装后：vision-kit-mcp
```

通过 [opencode.example.json](opencode.example.json) 接入 opencode（DashScope qwen3-vl-flash 示例）。

---

## DeepSeek Harness 视觉插件

与 opencode 版同源的 DSH **固定** Cordis 插件（[dsh-plugin/vision-kit.dsh.js](dsh-plugin/vision-kit.dsh.js)），
给 DSH 的任意文本模型一双“眼睛”：注册 `vision_describe` / `vision_extract` 两个模型工具，
通过 `python -m vision_kit.cli` 复用本项目的全部视觉逻辑。
`vision_extract` 额外把 **几何自洽校验** 结果（`geo_checks`）返回给模型：三角形内角和、向量加法关系、
矩阵乘法维度、负值等，任一不通过会触发采样重试，重试耗尽后逐条展示 `✗ 不通过`，让文本 agent 能察觉"读数自相矛盾"而非盲信。

### 首次配置（必须）

DSH 插件从仓库根的 `vision.config.json` 读取视觉凭证。出于安全考虑，**这个文件不会被项目分发**，需要你在自己机器上创建一份：

```bash
# 在仓库根目录
cp vision.config.example.json vision.config.json
# 然后编辑 vision.config.json，填入：
#   apiKey      —— 你在阿里云百炼创建的 SK- 开头 Key（必填）
#   python      —— （建议）你的 python.exe 绝对路径
#   visionKitDir—— 本仓库根目录的绝对路径（插件据此定位 cli.py 与 vision.config.json）
```

- 模板 [vision.config.example.json](vision.config.example.json) 不含密钥，可放心参考。
- ⚠️ `vision.config.json` 保存的是你的 API Key，参考项目配置即可，不要把它分享/上传。
- 插件**自动定位**项目目录：优先读 `VISION_KIT_DIR` 环境变量，其次由插件文件自身位置推导
  （本文件位于 `<仓库根>/dsh-plugin/vision-kit.dsh.js`，仓库根即上一级目录），再回退到当前工作目录探测。

### 安装与启用（固定插件，重启后保留）

本插件现在是 **DSH 固定插件**：作为组合行写进 `$DSH_HOME/profiles/web/cordis.patch.yml`，
随 DSH Web 部署启动自动挂载、重启后保留，**不需要**每次 `cordis_define` 重新定义：

```yaml
# $DSH_HOME/profiles/web/cordis.patch.yml
- insert:
    - id: vision_kit
      name: 'file:///D:/libs/vision_kit/dsh-plugin/vision-kit.dsh.js'
```

- `name` 必须是 `file://` URL 形式：原始 Windows 路径 `D:\...` 会被 Node ESM loader 当作 `d:` scheme 拒绝。
- 插件文件是 CommonJS 模块（`module.exports`），工具通过 `ctx.tools.register(...)` 全局注册，
  挂载后所有会话立即可用（新开会话生效；已打开的会话可在下次启动后看到）。
- 等价的手动 overlay 写法见 [dsh-plugin/cordis.yml](dsh-plugin/cordis.yml)（`dsh web --patch` 用）。
- 修改插件代码后需要重启 `dsh web`（编辑 patch 文件本身可触发热重载）。

### 路径

`image_path` 支持相对路径（基于调用方会话工作区）或绝对路径。

### 样例图的模型说明（重要）

README 各处"试一下"用的 `test_figure.png` 识别结果，是按**默认模型 `qwen3-vl-flash`**（DashScope）标定的。
不同视觉模型对同一张图的抽取可能有细微差异，若你换成 glm 等，结果可能不完全一致，属正常现象。

### 试一下

```text
用 vision_describe 看一下 test_figure.png，告诉我图里有哪些向量
用 vision_extract 提取 test_figure.png 中的结构化数据
```

> `test_figure.png` 在本仓库根目录。用全局插件 / 在别的工作区跑时，请换成你自己图片的绝对路径。

---

## 环境变量

| 变量 | 必填 | 默认 |
| --- | --- | --- |
| `VISION_API_KEY` | 是 | - |
| `VISION_API_BASE` | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `VISION_MODEL` | 否 | `qwen3-vl-flash` |
| `VISION_TIMEOUT` | 否 | `60` |
| `VISION_MAX_TOKENS` | 否 | `8192`（qwen3-vl-flash 上限） |
| `VISION_KIT_DIR` | 否 | 无。DSH 插件项目目录 + `PYTHONPATH`；插件优先读它定位项目，未设置则自动探测/按模板引导配置 |

### 参数调优建议

环境变量与引擎常量已给出合理默认值，**大多数场景无需改动**。若你有特定需求，可按下面调整：

**关于 max_tokens**
- 默认 `8192` 是 `qwen3-vl-flash` 的**上限**。视觉结构化输出通常是**短 JSON**（向量/矩阵/说明），
  实际用量往往只有几百到一两千 token —— 所以即使调小到 `1024`~`2048` 也极少截断，还能稍微降成本。
- 若一次要返回**很大的矩阵**、或图内**文字很多**，担心被截断，就保留 `8192`；需要更多才另说。
- ⚠️ **换模型时务必同步改这个值**：推荐默认按 qwen3-vl-flash（上限 8192），
  若改用智谱 `glm-4v-flash`，其上限 **只有 1024**，用 8192 会直接报参数超限 —— 记得 `VISION_MAX_TOKENS=1024`。

**针对不同使用倾向**

| 你的诉求 | 建议调整 | 说明 |
| --- | --- | --- |
| 更准（容忍慢） | `VISION_TIMEOUT` 调大，如 `120` | 图片预处理+补漏重试+分块可能较慢，超时太紧会误判失败 |
| 更省成本 | `VISION_MAX_TOKENS` 调到 `1024`~`2048` | 结构化输出普遍够用，可减少付费 token 溢出浪费 |
| 图很小/字很密 | 调大引擎 `MIN_DIM`（engine.py，默认 `1000`） | 小图自动放大，放大得越足数字越清楚 |
| 超大图（会分块） | 调小引擎 `MAX_TILE_SIDE`（默认 `1600`） | 块越小单模型压力越小，但块变多、开销变大 |
| 补漏更彻底 | 调大引擎 `MAX_ATTEMPTS`（默认 `3`） | 维度/几何校验不通过时重试次数，越多越稳但越慢 |

> 环境变量（`VISION_*`）通过 **CLI / MCP / opencode 插件的 env** 直接改即可，无需动代码；
> 引擎常量（`MIN_DIM` / `MAX_TILE_SIDE` / `MAX_ATTEMPTS` 等）在 `engine.py` 顶部修改后重新调用即可。

---

## 开发

```bash
pip install -e ".[dev]"
pytest              # 单元测试（tests/）
ruff check .        # 代码质量

# 基准（2.1）：重新生成题图（确定性，无需 API）
python benchmark/generate_images.py           # 主集 + 对抗集 + 困难集
# 跑评测（需要 VISION_API_KEY）：逐图指标 + REPORT 榜单
python benchmark/eval.py --limit 3            # 快速试跑
python benchmark/eval.py                      # 主集全量 → REPORT.md
python benchmark/eval.py --subset adv         # 对抗注入集 → REPORT_ADV.md（校验层防护率）
python benchmark/eval.py --subset hard        # 困难集 → REPORT_HARD.md（扰动下补漏重试价值）
python benchmark/eval.py --baseline           # 额外跑单次直出基线，对比校验层增益
# 回归快照（2.4）：记录/比对真实模型输出基线（需要 VISION_API_KEY）
python benchmark/eval.py --update-snapshots
pytest tests/test_snapshots.py               # 数值级回归比对（离线自动跳过）
```

## 项目结构

```
vision_kit/
├── __init__.py       # 包入口，导出 VisionClient（__version__ = 0.3.0）
├── client.py         # VisionClient：放大预处理 / 结构化 / 分块合并校验 / 定向重试 / 渲染回写 / 内容寻址缓存
├── engine.py         # 识别引擎：提示词、参数化调用、定向重试（失败规则→提示）、超大图分块合并再校验
├── figure.py         # FigureData：图元 DOM（含圆上点/四边形）+ 向量/矩阵解析 + 维度/引用校验 + 几何校验 + 分块合并 + 渲染回写 + TikZ
├── geometry.py       # 几何自洽校验：三角内角和 / 向量加法 / 矩阵维度 / 线段长度 / 三角形不等式 / 勾股 / 圆定理 / 相似 / 平行四边形 / 函数对称 / 负值（纯逻辑）
├── stats.py          # 统计图 → 数据表（4.3）：类别/系列数值 + 长度对齐/百分比求和/非负校验 + 重试
├── prompts.py        # 场景化提示词模板（4.1）：geometry / function / vector / statistics
├── cli.py            # 命令行 JSON 入口（--mode describe|extract|both|render|stats，--type/--tikz/--render）
├── mcp_server.py     # opencode MCP 服务器（stdio；describe_image / structured / stats）
├── benchmark/        # 2.1 基准：合成题图 + 对抗注入集 + 困难集 + ground_truth* + eval.py（--subset/--baseline，产出 REPORT*.md）
├── tests/snapshots/  # 2.4 回归快照基线（真实模型输出，数值级比对）
├── opencode.example.json
├── vision.config.example.json   # DSH 插件配置模板（不含 Key，可直接用）
├── vision.config.json           # 本机视觉凭证（由模板复制后填写，属本地文件，不随项目分发）
├── .gitignore          # 已排除 vision.config.json 等本地文件
├── dsh-plugin/
│   ├── vision-kit.dsh.js       # DeepSeek Harness 固定视觉插件（CommonJS 模块）
│   └── cordis.yml              # 等价 overlay（dsh web --patch 用；固定安装见 $DSH_HOME/profiles/web/cordis.patch.yml）
└── .opencode/
    └── plugins/vision-kit.js   # opencode 视觉插件（见上文）
```
