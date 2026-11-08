# M1/M5 第一版：功能验证证据包

记录日期：2026-11-08；运行所用实现为 `64207ed`。输入清单中的 19 个优化器模块指纹已与文档整理时的代码逐项比对一致。

这里保存用于团队核查的摘要、实例和检查日志，不是正式论文效果实验。真实 LLM 调用数为 0，没有使用独立事件验证或最终测试。

| 文件 | 内容 |
|---|---|
| [manifest.json](manifest.json) | 文件大小及 SHA-256、记录范围 |
| [verification.json](verification.json) | 本地工程检查结果汇总 |
| [synthetic-summary.json](synthetic-summary.json) | 合成面板三轮运行摘要 |
| [historical-summary.json](historical-summary.json) | 公开 NFL 历史开发面板三轮运行摘要 |
| [fallback-summary.json](fallback-summary.json) | 调用预算为零时的连续运行摘要 |
| [feedback-to-candidate-example.json](feedback-to-candidate-example.json) | 一条证据、建议、实际修改和父子评价的完整对应 |
| [historical-input-manifest.json](historical-input-manifest.json) | 历史输入来源、数据指纹、代码指纹与可用性假设 |
| [teammate-core-unchanged.json](teammate-core-unchanged.json) | 与 integration `2d2a072` 一致的 8 个核心模块指纹 |
| [pytest.log](pytest.log) | 3238 项通过、232 项按条件跳过的本地日志 |
| [mypy.log](mypy.log) | 535 个源文件的严格类型检查 |
| [imports.log](imports.log) | 10 条架构依赖规则检查 |
| [dashboard.log](dashboard.log) | 27 个文件、66 项前端测试及覆盖率输出 |

日志中的本地工作目录替换为 `[repository]` / `[workspace]`，并去除行末空白；诊断内容保留。检查记录属于本地验证，不能替代 GitHub CI 状态。

历史面板有 1 个独立事件、2 个合约市场、32 个观测点。归档事件时间用作回放可用时间代理，真实历史接收时间未验证。原始历史面板不在本证据包中，单凭摘要无法重跑历史回放；需另行取得与输入清单指纹匹配的规范化开发输入。

合成功能运行可以直接根据 [运行说明](../../m15-evidence-loop.md) 重跑。固定种子应重现候选与诊断；耗时等运行环境相关字段不要求逐字节一致。

论文方法、结果表述边界与下一步实验见 [方法与证据说明](../../m15-paper-method-and-evidence.md)。
