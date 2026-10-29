# 第 2 轮：M5 基础复盘

本报告用于开发流程检查；不是因果归因、真实收益证明或最终样本外评价。

候选：8；有开发内慢评结果：3；晋级：1。

| 候选 | 层级 / 父代 | 权重 / 入场门槛 | 方向准确率 | Brier | 快评综合分 | 开发内后半窗口 PnL | 状态 / 原因 |
|---|---|---|---:|---:|---:|---:|---|
| 7999c3450338832008089fc23305941835570a31cc74317aaca9fa9042ee9c79 | L2 / bd60945c7975ae2b003cba122a1b94b6a504313324cfed98f7f418ae1fdd8f24 | orderbook_imbalance=0.25, metaculus_prior=-0.25 / edge=0.02 | 0.703125 | 0.199803 | 0.503322 | 未评价 | 未进入慢评（未入 Top-K） |
| 8271e8f734e425220f131a60b7e6b120e201ae9dc29e898d82c850b118fd9247 | L2 / e272d115e349ea65c280976f65626507dc4567366fa84ed5c52670c1cf2e8d28 | orderbook_imbalance=0, metaculus_prior=0 / edge=0.05 | 0.718750 | 0.201262 | 0.517488 | 0.000000 | non-positive out-of-sample pnl |
| 3d4b5942dd85646434f9d950f3405a59e15aab07cbd17d1bf8a76effdb7bd81e | L2 / b8f95110c9d41f6c62c8e4db9091e15c5b639d9787b8a4f3c8f3399cd8b00745 | orderbook_imbalance=0.25, metaculus_prior=0 / edge=0.02 | 0.703125 | 0.193846 | 0.509279 | 2.422951 | promoted |
| c7c6dbb60695ae7515d5023750a9775ae231c6059de90a27697f073cc6509cb0 | L2 / bd60945c7975ae2b003cba122a1b94b6a504313324cfed98f7f418ae1fdd8f24 | orderbook_imbalance=-0.25, metaculus_prior=-0.25 / edge=0.02 | 0.656250 | 0.224564 | 0.431686 | 未评价 | 未进入慢评（未入 Top-K） |
| cf2c62cc62be2270c20ac16a7f140fc4921d96c70d1966d6203bf9509b69b179 | L2 / b8f95110c9d41f6c62c8e4db9091e15c5b639d9787b8a4f3c8f3399cd8b00745 | orderbook_imbalance=-0.25, metaculus_prior=0 / edge=0.02 | 0.671875 | 0.214978 | 0.456897 | 未评价 | 未进入慢评（未入 Top-K） |
| 5b8cb3e4186a8a58a8b06f02a7484d2b94ff372910dc8a53d2df53e39ff5ae52 | L2 / b8f95110c9d41f6c62c8e4db9091e15c5b639d9787b8a4f3c8f3399cd8b00745 | orderbook_imbalance=0, metaculus_prior=0.25 / edge=0.02 | 0.718750 | 0.194737 | 0.524013 | 0.487334 | composite 1.2499 <= incumbent 3.1937 |
| 73e8d16f02cd7d5ab1732739c8b1cd09131bef34ed62802aaac7965dc59bfb4c | L1 / 无 | orderbook_imbalance=-0.5, metaculus_prior=0 / edge=0.02 | 0.625000 | 0.234831 | 0.390169 | 未评价 | 未进入慢评（未入 Top-K） |
| fac4a5269e2d5620164824fb17726b51c9557cc1d662e81c1d6e99e5ee4ac30e | L1 / 无 | orderbook_imbalance=-0.25, metaculus_prior=-0.5 / edge=0.1 | 0.625000 | 0.235328 | 0.389672 | 未评价 | 未进入慢评（未入 Top-K） |

## 配置使用情况

- metaculus_prior：8 个候选启用。
- orderbook_imbalance：8 个候选启用。

以上仅是启用因子频数，不是实际触发次数、贡献值或因果效应。

## 基础复盘与下一轮

- 未进入慢评的候选记为未评价，不能解释为零收益或慢验收失败。
- 部分候选开发内后半窗口收益非正；仅凭该数字不能判断是信号、成本还是样本问题。
- 本轮更新了本地研究冠军；没有注册或激活线上策略。
- 下一轮 M1 依据开发快评选择父代，进行有限变异并保留随机探索。

## 评价边界

队友字段中的 oos 是开发内部切片：同一面板已经参与 M2 筛选，不能作为独立最终测试。
当前 M3 是每点一份合约的简化模拟；覆盖率、门槛调用与时序隔离仍需单独完善。
原系统 Evaluator 的成交与归因证据尚未接入。本报告不自动推断具体因子应增加或减少。
