# tests/snapshots — 回归快照（2.4）

本目录存放**真实视觉模型**在基准图上跑出的抽取结果快照（`<id>.json`），
用于「改提示词 / 校验规则 / 引擎常量后不回归历史能力」的数值级比对。

## 使用流程

```bash
# 1.（首次 / 人工确认改进后）记录基线 —— 需要 VISION_API_KEY
python benchmark/eval.py --update-snapshots
# 或
pytest tests/test_snapshots.py --update-snapshots

# 2. 日常回归（离线自动跳过）
pytest tests/test_snapshots.py
```

## 约定

- 比对是**数值级**（向量/矩阵/点坐标逐分量，容差 1e-6），不是字符串比对；
- 快照缺文件 / 数值失配 / 新增标注都会导致测试失败，用于暴露无意的行为漂移；
- 重新记录基线前，请人工确认新输出确实更好（快照不是"自动覆盖"，是"人工确认后重录"）。

## 为什么不用 ground_truth 当基线

`benchmark/ground_truth.jsonl` 是**理想答案**（评测用）；快照是**当前引擎的真实输出**
（回归用）。两者用途不同：评测衡量"离理想答案多远"，回归衡量"与历史行为差多少"。
