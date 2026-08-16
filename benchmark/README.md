# benchmark — 结构化题图识别基准（2.1）

vision_kit 的评测基准：**同一套图、同一套 prompt 模板**，量化"读得准不准"，
并把结果沉淀为可引用的榜单（`REPORT.md`）。

## 目录结构

```
benchmark/
├── images/                 # 合成题图（deterministic 生成，可复现）
│   ├── adv/                # 对抗注入集：矛盾标注图（校验层必须抓出）
│   └── hard/               # 困难集：缩放/噪声/JPEG/旋转/低对比度扰动图
├── ground_truth.jsonl      # 主集 GT：每行一条 {id, image, type, vectors, matrices, figures, note}
├── ground_truth.adv.jsonl  # 对抗集 GT：额外带 expect_error（期望校验层报出的错误码）
├── ground_truth.hard.jsonl # 困难集 GT：数值与主集一致（扰动不改内容）
├── generate_images.py      # 重新生成全部子集（无需 API）
├── eval.py                 # 评测脚本：跑引擎 → 算指标 → 写 REPORT.md / REPORT_ADV.md / REPORT_HARD.md
└── README.md               # 本文件
```

## 复现步骤

```bash
# 0.（可选）重新生成基准图：确定性，重复运行输出一致
python benchmark/generate_images.py            # 主集 + 对抗集 + 困难集
python benchmark/generate_images.py --main     # 只主集
python benchmark/generate_images.py --adversarial  # 只对抗集
python benchmark/generate_images.py --hard     # 只困难集

# 1. 配置视觉模型（与 CLI 同一套环境变量）
export VISION_API_KEY=你的Key          # 必填
export VISION_MODEL=qwen3-vl-flash     # 可选

# 2. 跑评测（先 --limit 3 快速试跑，再去掉 limit 全量）
python benchmark/eval.py --limit 3
python benchmark/eval.py               # 全量，写 REPORT.md

# 2.1 对抗注入集：校验层"防护率"（矛盾标注不得被直出自洽）→ REPORT_ADV.md
python benchmark/eval.py --subset adv

# 2.2 困难集：扰动下图读得准不准、补漏重试的价值 → REPORT_HARD.md
python benchmark/eval.py --subset hard

# 2.5 同时跑"单次贪心直出"基线（不重试/不校验），在榜单里对比确定性层增益
python benchmark/eval.py --baseline

# 3. 同时记录回归快照（2.4 基线）
python benchmark/eval.py --update-snapshots
```

## 指标定义

| 指标 | 定义 |
| --- | --- |
| 数值准确率 | 与 ground truth 逐分量匹配（容差 1e-6）的比例；向量/矩阵拍平后按名比对，含 figures.points 坐标 |
| 召回 | 识别出的标注名数 / ground truth 标注名数 |
| 幻觉数 | 识别出但 ground truth 中不存在的标注名数量（VLM 编造数字的直接信号） |
| 一致性率 | `consistent=True` 的占比（维度一致 + 图元引用完整） |
| 几何自洽率 | `geo_checks` 全部通过的占比（三角内角和 / 向量加法 / 线段长度 / 值域 / 圆定理 / 勾股等） |
| 重试率 | 需要 ≥2 次模型调用的占比（维度/几何校验不通过触发的采样补漏重试） |
| 平均调用次数 | 每张图平均调用模型次数（首轮贪心 1 次起） |
| 平均延迟 | 每张图端到端耗时（秒） |
| **防护率**（对抗集） | error_code ≠ OK 或触发重试的占比 —— 校验层把矛盾读数拦下的比例（越接近 1 越好） |

## 数据设计说明

### 主集（12 张）

- 覆盖：向量图（平行四边形法则 / 加法）、几何三角（坐标 + 边长 + 内角）、
  矩阵（2x2 / 3x3）、圆（圆心 + 半径 + 圆上点）、坐标点、函数图、向量+矩阵组合；
- 图内标注与 ground truth **逐字一致**，且 GT 自身满足全部几何校验
  （如三角内角和 = 180、线段长度 = 端点坐标距离）——引擎若读错数，会被确定性校验直接抓出；
- 基准图面向"数字标注转录 + 自洽校验"链路（vision_kit 的核心能力），
  不含人工评分的开放性问答。

### 对抗注入集（6 张，`--subset adv`）

- 在合成图上标注与几何自洽**相矛盾**的数值（三角内角和 = 190、三边 3/4/9 违反三角形不等式、
  直角 + 三边 3/4/6.5 违反勾股、向量加法近失、矩阵维度不齐、圆上点到圆心距离 ≠ 半径）；
- 任何模型读出的数字必然不自洽 → 校验层**必须**报出
  `GEOMETRY_INCONSISTENT` / `DIMENSION_MISMATCH` 或触发采样重试；
- 指标是**防护率**：`error_code ≠ OK` 或 `retried` 的占比。防护率 100% 证明
  「确定性层确实在 VLM 输出之后拦住矛盾读数」——这是校验层价值的杀手级证据；
- 对抗集 GT 自带 `expect_error` 字段，离线单测（`tests/test_benchmark.py`）即验证
  「对抗样本确实会被校验层抓出」，不依赖真实 API。

### 困难集（12 张，`--subset hard`）

- 每张主集图施加一种确定性扰动（缩小 + JPEG 压缩 / 旋转 2-5° + 低对比度 / 高斯模糊 + 缩小 + JPEG）；
- GT 与主集数值一致（扰动不改内容）——读得准不准、补漏重试的价值在此显现
  （主集直出全对时，困难集才是校验 + 重试的用武之地）。

## 对比其他插件

`REPORT.md` 的榜单目前为 vision_kit 单引擎（12 图真实数据 + `--baseline` 内置基线对比）。
要加入 `dsh-vision` / `see_image` / `dsh-plugin-deepeye` 等竞品时，为它们实现同样的 caller（输入图 + 通用结构化提示词 → 文本），
再在 `eval.py` 里追加一行即可——同一套图、同一套 prompt，指标可直接横向对比。

**内置基线（`--baseline`）**：同一套图、同一 prompt，但只做**单次贪心直出**
（不重试、不校验）——主链路与基线的准确率/幻觉数差值，即
「确定性层（维度校验 + 几何自洽 + 采样补漏重试）」的可量化增益。

## 与回归快照（2.4）的关系

- 本基准是**对外评测**（评价引擎好不好）；
- `tests/snapshots/` 是**开发期回归**（改提示词/规则时不倒退），
  基线由 `eval.py --update-snapshots` 写入，`pytest tests/test_snapshots.py` 做数值级比对；
- CI（有 `VISION_API_KEY` 时）会跑快照比对 + 主集/对抗集 smoke（`--limit 3`），
  防止提示词/规则改动导致的能力倒退。
