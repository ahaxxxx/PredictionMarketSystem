# M1/M5 证据反馈闭环：第一版实现与学习入口

2026-11-08；基于 integration 的 `2d2a072`。本分支为 `feat/optimizer-m15-evidence`。

论文材料与团队核对入口：[已实现机制、验证证据与论文表述](m15-paper-method-and-evidence.md) · [运行和检查证据](evidence/m15-v1/README.md)。

## 这次完成什么

现在离线优化器可以执行：

```text
M1 生成候选
 → 现有 M2/M3/M4 评价与归档
 → M5 从搜索特征及已有快评构建证据、诊断、修改建议
 → 下一轮提议器引用这些建议，编译为原来的 CandidateSpec
 → 检查引用、参数方向、合法性及预测/拟交易行为变化
 → 登记候选，再进入现有评价器
```

这是离线研究子循环。交易平台的 Sensor/Controller/Actuator/Evaluator 并发架构没有改变。

| 能力 | 状态 |
|---|---|
| 原有 L1 随机、L2 进化 | 保留；默认 baseline 模式 |
| M5 因子有效激活率、预测边界比例、已有 Brier 的证据记录 | 已实现 |
| M5 确定性诊断和结构化修改建议 | 已实现 |
| L3 单参数提议的解析、验证、登记、失败后退回 L2 | 已实现 |
| 离线参考响应与固定响应文件 | 已实现；真实 LLM 调用为零 |
| 真实模型、LLM 自由文本诊断、token/费用预算 | 后续接入；当前没有网络供应商适配器 |
| 双父代重组、任意策略代码生成 | 未实现；本版只改权重或全局 edge |
| 独立事件的 M3 验证集 | 等队友接口；本入口仍使用原开发面板切片 |
| 独立最终测试或泛化提升 | 本轮没有进行 |

当前的 L3 标记表示走了提议编译路径，是否为真实模型必须同时看后端和 `llm_calls`。`offline-evidence` 是规则参考实现，不能写成 LLM 实验结果。

## 建议按这个顺序读代码

1. `research_contracts.py`：证据、诊断、建议、提议请求和参数修改的记录格式。
2. `evidence.py`：只接收特征，不接收结算结果；计算有效输入、预测与拟交易行为。
3. `m5.py` 的 `build_review()`：把证据整理为下一轮建议。
4. `llm_proposer.py` 的 `GuidedProposer.propose()` / `_compile()`：读取建议、检查和编译参数修改。
5. `m1.py` 的 `register_candidates()`：把新提议登记到 L1/L2 共用的配置表。
6. `run.py`：把每一轮产生的 `review` 传给下一轮，并保存结果。
7. `tests/unit/test_optimizer_evidence_loop.py`：每个测试都是一个可运行的具体例子。

`CandidateSpec` 仍是下游使用的策略配置；新的研究记录在旁边保存，不要求队友改公共数据格式。旧的 `proposer.py` / `report.py` 占位文件未被误当成运行入口。

## M5 怎么得到建议

对于标量因子，先看其实际输入是否非零；对于概率因子，按现有组合器检查“因子概率 − 市场概率”，避免把一个等于市场概率的先验误判成有修正信号。

- 有效输入全部为零：建议本轮局部修改暂不改变这个权重。
- 预测触及概率边界，且该权重非零：提出向零调整幅度的待检验建议。
- 其余情况：允许比较权重。
- 入场门槛：允许比较，但必须检查是否真的改变了预测或拟交易行为。

这些规则不保证收益改善。边界比例来自组合后的预测，不能据此宣称某一个因子导致了错误。本版没有自动做因果归因、成本归因或新增收益消融。

每条证据包含数据指纹、轮次、候选、指标定义、数值、样本数和使用范围。已有 Brier 来自 M2；因为旧 `FeedbackPayload` 没有评分点数，证据中的样本数标为未知，不假设一定评价了整块面板。

## M1 怎么消费建议

每个提议必须包含父候选、目标参数、新值、建议 ID、证据 ID、参数增减方向和简短假设。

程序依次检查：

1. 响应是受限的 JSON；字段、数量、数值和引用符合约定。
2. 父候选和建议匹配，证据真实存在。
3. 目标未被冻结，新值在原网格内，声明的增减方向与实际修改一致。
4. 修改后的配置未被登记过。
5. 在同一搜索特征上，预测或拟交易方向/是否入场确有变化。

通过后才登记为 L3 候选。模型解释中的自然语言经济逻辑尚未被完整验证；当前方向检查明确指参数增减，不等于理解了全部经济语义。

无效提议被记录并由 L1/L2 补足当前候选预算；每轮至多请求后端一次，没有无限重试。正常批次保留至少一个 L1/L2 名额。被冻结的维度仍可由随机探索覆盖，不永久从空间删除。

所有新候选进入同一个登记表，因此收到 L3 反馈后，L2 仍能继续工作。登记前核对规范参数及配置 ID，不允许用不同解释把同一配置冒充成新候选。

## 如何运行

在仓库根目录安装锁定依赖后运行。每次使用一个新的输出目录，避免覆盖旧实验。

```bash
uv sync --frozen

# 原 L1/L2 + 新的确定性 M5 证据报告
uv run python -m pms.optimizer.run --demo --output .data/m15-baseline

# 证据驱动离线响应：检验 M5 -> 提议编译路径，不调用模型
uv run python -m pms.optimizer.run --demo --proposal-mode offline-evidence --output .data/m15-guided

# 检验预算耗尽仍可完成
uv run python -m pms.optimizer.run --demo --proposal-mode offline-evidence --proposal-call-budget 0 --output .data/m15-fallback
```

已有规范化历史输入仍可通过 `--input` 代替 `--demo` 接入。没有自动抓取新数据，也没有自动指定队友尚未确认的事件划分。

固定响应模式使用 `--proposal-mode scripted --proposal-responses responses.json`。文件为原始响应字符串数组，每个字符串形如 `{"proposals": [...]}`；具体字段参考 `ProposalEdit`。响应中的父 ID 和证据 ID 必须来自上一轮真实记录。

## 去哪里检查闭环是否真的发生

| 输出文件 | 看什么 |
|---|---|
| `iterations/001/diagnostics.json` | 第一轮证据、诊断和建议 |
| `proposal_calls.json` | 下一轮请求是否携带上一轮 review；原始响应及调用状态 |
| `proposal_attempts.json` | 每个参数提议为什么接受或拒绝；接受后的候选 ID |
| `iterations/002/proposals.json` | 第二轮是否包含该候选 |
| `iterations/002/results.json` | 它经过原评价器后得到什么结果 |
| `reports/iteration-002.md` | 适合人工阅读的结果与建议 |
| `summary.json` | 候选数、L1/L2/L3、真实/离线调用、额外特征回放数量 |
| `input_manifest.json` | 数据指纹、全优化器代码指纹及队友核心文件指纹 |

额外的特征检查不伪装成免费操作：M5 每候选一次特征回放；提议行为比较每次包含父子各一次特征回放。没有为这些检查额外读取收益标签。语法错误等在回放之前拒绝。

## 如何理解当前数据边界

`FeatureView` 只接受显式 search 范围，且不含 OutcomeStore。历史加载器仍检查观测、可用、决策、结算与研究截止时间。这些检查不能证明一份被人为错误标记的数据真的没有污染，仍需要队友提供可信事件清单。

本入口的 M3 尚未改成独立开发验证事件。输出明确保留 `m3_data_protocol=legacy_same_development_panel_slices`、`independent_validation_used=false`、`final_holdout_used=false`。因此这里完成的是框架和开发回放，不能据此宣布新方法泛化更好。

下一步与队友确认 M2 搜索输入、M3 验证输入和事件族清单，再共同扩展调用边界。真实模型接入后，另做费用/输出上限、超时、模型版本和用量记录，并比较 EA、数值反馈 LLM、证据反馈 LLM。不能把本版规则策略的效果归给 LLM。

## 验证命令

```bash
uv run pytest -q
uv run mypy src/ tests/ --strict
uv run lint-imports
cd dashboard
npm ci
npm run test:ci
```

新测试覆盖证据激活口径、有效修改、反馈消费、伪造引用、方向冲突、网格越界、重复、无行为变化、原子登记、响应错误、调用预算、退回 L2、开发范围检查和同种子重放。
