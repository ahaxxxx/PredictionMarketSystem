# M1/M5 无 LLM 多轮接入：队友讨论入口

团队汇报与讨论清单：[项目进展、最新 IC 分支与待确认事项](optimizer-team-discussion.md)。可在 [PR #1](https://github.com/ahaxxxx/PredictionMarketSystem/pull/1) 留言讨论。

新增历史数据入口：[直接下载公开样例并跑一轮](optimizer-historical-input.md)。原合成演示保持兼容。

## 一条命令运行

从仓库根目录执行，需要 Python 3.13 与 uv：

```bash
uv sync --frozen
uv run python -m pms.optimizer.run --demo \
  --rounds 3 --batch-size 8 --top-k 3 --seed 42 \
  --output .data/optimizer-demo-001
```

选择一个新的输出目录；非空目录会拒绝覆盖。`--demo` 必须显式提供，表示输入是合成开发数据。没有API密钥、LLM、PostgreSQL或交易账户依赖。

## 本次增加了什么

| 文件 | 职责 |
|---|---|
| src/pms/optimizer/m1.py | L1随机、L2有边界的相邻变异；父代配置表、稳定配置ID、去重与耗尽处理 |
| src/pms/optimizer/m5.py | 将原FeedbackPayload输出为模板复盘，可附真实晋级理由；不编造贡献归因 |
| src/pms/optimizer/run.py | 生成开发样例，调用真实run_once，串联3轮并持久化结果 |
| tests/unit/test_optimizer_m1_m5.py | 多轮、父代、复现、隔离、缺失、耗尽与异常记录测试 |

原schemas.py、replay.py、fast_loop.py、slow_loop.py、champion_pool.py、loop.py、tracer.py均不修改。原proposer.py/report.py占位函数也不修改；新运行入口显式导入m1.M1Proposer与m5.render_report。不会自动修补或替换原占位函数。

这份分支基于feat/optimizer-m234的3430fe8b，仅增加M1/M5及入口，不混入此前独立research/optimization实验框架。

## 一轮是怎样连接的

```text
M1Proposer.propose(previous_feedbacks, n)
  → CandidateSpec[]
  → OptimizationLoop.run_once(candidates, development_panel, outcomes)
  → M2 快评分 → Top-K → M3 简化模拟 → M4 本地冠军
  → CandidateResult[] / FeedbackPayload[]
  → M5 Markdown报告 + 原反馈JSON
  → 同一个M1实例生成下一轮
```

这是离线研究调度，不改变原Sensor/Controller/Actuator/Evaluator并发运行方式。

## M1 第一版规则

- 固定orderbook_imbalance与metaculus_prior，role=rule_delta；required=True。
- 权重网格{-0.5,-0.25,0,0.25,0.5}，交易优势门槛{0.02,0.05,0.10}，共75个配置。
- 首轮包含零权重市场基准，其余随机抽取。
- 后续按历史开发快评选前3名为父代，优先约75%相邻单维变异，其余随机补充；邻域不足时随机补足。
- M1实例保存candidate_id→完整配置；不依赖反馈反推父代，不使用模块全局状态。
- 配置ID只由steps和edge_threshold决定，父代和层级不影响配置身份。
- 只用fast_composite选择父代，不用oos、promoted等字段选择父代；没有冠军也能继续。
- 空间耗尽时返回不足n个或空列表；不会重复评价、死循环或虚报数量。
- L3尚未启用；整个入口LLM调用为0。

## M5 第一版边界

模板包含成绩表、父代、参数、慢评是否存在、晋级结果与真实PromotionDecision.reason。原FeedbackPayload不扩字段；具体晋级理由另存feedback_context.json。

exercised_factors只解释为配置启用频数，不是实测贡献。None显示“未评价”，不能当作零收益或慢验收失败。没有真实证据时不建议提高/降低某个特定因子。原Evaluator的丰富归因证据尚未接入。

`use_llm=True`目前同样返回模板并明确说明未配置，不会发出模型调用。

## 运行输出

```text
run_config.json                 参数、搜索空间和开发演示声明
input.json / input_manifest.json 合成输入及数据/队友核心代码哈希
candidate_registry.json         全部已提出的候选卡
iterations/001/proposals.json   本轮提议
iterations/001/results.json     完整快评/慢评/晋级/反馈
iterations/001/feedback.json    原FeedbackPayload
iterations/001/feedback_context.json 真实晋级理由
iterations/001/counts.json      快评/慢评/晋级数量
reports/iteration-001.md        M5基础复盘
champions.sqlite3              队友M4档案
traces.sqlite3 / trace_tree.json 队友运行追踪
champion.json                  冠军快照；未注册、未激活
summary.json                   总轮数、评价数、L1/L2/L3与调用数
```

不支持中断后自动恢复，关键异常记录failed并重新抛出。不要混用不同数据/成本条件的冠军库。

## 演示与真实研究的区别

输入是先生成信号、再按潜在概率采样结果的合成数据，保留观察与结算时间说明。所有输入仅用于回溯开发。没有独立最终测试面板进入搜索，也未进行真实交易。

原M3的oos命名仍表示开发内部后半切片：同一完整panel已参与M2筛选。报告中显式注明不等同独立样本外证据。M1不使用其慢评分作父代适应度，但这本身并未修复M2/M3的数据切分。

当前M3每点交易一份合约，NO价=1-YES价，费用/滑点为简化模型；不是完整资金与盘口成交回测。无真实收益或论文效果主张。

## 验证

```bash
uv run pytest -q tests/unit/test_optimizer.py tests/unit/test_optimizer_m1_m5.py
uv run mypy src/ tests/unit/test_optimizer_m1_m5.py --strict
uv run lint-imports
```

2026-10-28：3轮、24个唯一候选，L1=12、L2=12、L3=0，LLM=0。每轮8个快评、3个慢评。实际调用队友模块并形成一次本地冠军版本，无注册或激活。示例摘要和第二轮报告见相邻optimizer-m15-example目录。

基线已存在的strict mypy失败：tests/unit/test_optimizer.py第192/202/216/223/229行共5个错误（未标注返回类型、调用未标注函数、None未收窄）。本次不改队友原测试、不添加ignore。新增实现与新增测试的严格类型检查应通过；全仓类型门槛仍需队友处理这5处基线错误。

## 建议队友重点看三处

1. 是否认可M1的固定因子/角色、权重与门槛范围，以及父代状态放在实例中？
2. 是否认可第一版由loop生成结构化反馈，M5先做展示与基础复盘？若需要可执行诊断，应协商扩展反馈字段。
3. 请后续一起处理：M2覆盖率分子/分母单位不一致，主循环未调用快/慢passes_gate，以及独立开发/验证/最终测试切分。当前保留原实现便于对照，功能演示不代替修复。
