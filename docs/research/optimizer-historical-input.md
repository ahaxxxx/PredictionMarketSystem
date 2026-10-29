# 接入外部历史数据：一轮可复现的运行

2026-10-29。现在既可以跑原合成演示，也可以直接读取历史开发面板。此次实际下载并使用公开 PredictionMarketBench 归档，不依赖原项目数据库或交易账户。

## 两条命令

在仓库根目录执行，先 `uv sync --frozen`：

```bash
uv run --with pyarrow==26.0.0 python scripts/fetch_optimizer_history.py \
  --output .data/public-history-source-001

uv run python -m pms.optimizer.run \
  --input .data/public-history-source-001/development.json \
  --rounds 1 --batch-size 8 --top-k 3 \
  --output .data/public-history-loop-001
```

每次使用新的空输出目录。下载脚本固定源版本与三个文件的SHA256；下载文件只保存在本地.data，不随本PR重新分发。若已有原始文件，可提供 `--raw-dir PATH`，跳过网络下载并保留同样的校验。

`pyarrow`仅用于一次性读取Parquet，不是主循环的新依赖。原合成数据命令 `python -m pms.optimizer.run --demo ...` 仍然可用，`--demo` 与 `--input` 二选一。

## 外部来源及实际样本

- 来源：https://github.com/Oddpool/PredictionMarketBench
- 固定源版本：611d66941717310858683278940df21c33c406f2。
- 事件：KXNFLGAME-26JAN11BUFJAC，归档自述为Kalshi比赛市场。
- 原始文件：metadata.json、settlement.json、orderbook.parquet，共8,047条订单簿行。
- 固定预处理：结算前、有效双边盘口、每合约每小时首个有效点；保留32个时间点、2个合约、1个事件组。
- 只使用确实存在的订单簿失衡和favorite_longshot_bias因子，不填造Metaculus先验。
- YES参考价格为bid/ask中点，不是保证可成交的买价；NO使用对应盘口推断YES ask。

脚本记录每个剔除原因、来源网址、文件哈希和价格口径。没有根据候选收益挑选样本。

## 一轮实际结果

8个候选快评，3个候选进入队友M3模拟，形成1个本地冠军版本，生成完整结果、SQLite档案、追踪和M5报告。LLM调用0，未注册策略或发生真实交易。

这说明“外部历史数据 → 因子面板 → M1—M5”可以运行；不代表该冠军有真实可交易优势。单一事件远不足以判断泛化。

## 历史输入约定

`--input`读取一个JSON，包含：

- dataset_id、source_kind、availability_assumption、as_of，以及明确的role=development。
- observations：sample_id、market_id、event_id、observed_at、available_at、decision_at、market_probability，以及[factor_id,value]列表。
- outcomes：对应sample_id、整数0/1结果，以及结果available_at。

在交给旧回放器前检查：有时区；信息在决策前可用；决策早于结算；结果在研究截止前可用；同市场结算一致；同市场同决策时刻不重复；因子完整且有限。拒绝将validation/test文件直接用于搜索。

M1搜索空间从输入实际因子确定，当前允许orderbook_imbalance、metaculus_prior、favorite_longshot_bias。不同开发集不能混用父代或冠军库。

## 必须保留的边界

1. 历史接收时间无法从该归档独立验证，目前以源事件时间作为可用时间代理，并在manifest和报告中注明。接口检查不等于证明上游数据没有历史泄漏。
2. 只下载development样例，未把先前的validation/test数据并入搜索。最终留出数据没有用于这轮循环。
3. 旧M3的OOS字段仍是开发内部切片，旧M2覆盖率/门槛调用问题尚未在此修复。
4. 执行仍为队友简化模型；价格中点、NO互补价、费用/滑点和每点一份合约不等同交易所真实成交。
5. 这是离线公开归档适配，不是新增Kalshi在线行情或交易适配器。原系统Kalshi运行适配器仍未实现。
6. 后续接Polymarket历史价格、交易数据或其他归档时，先统一上述字段；只有价格序列不足以还原历史订单簿或外部先验。

## 实现位置

- src/pms/optimizer/data.py：严格输入检查、FactorPanel和OutcomeStore适配。
- src/pms/optimizer/run.py：增加互斥的--input入口，共用现有多轮调度。
- src/pms/optimizer/m1.py：增加FLB可用维度；原默认合成空间不变。
- scripts/fetch_optimizer_history.py：固定版本下载、校验和归档转换。
- tests/unit/test_optimizer_historical_input.py：9项输入与接通测试。

队友M2/M3/M4、原schemas与loop文件保持原样。
