"""M5 first version: deterministic report of the existing feedback contract."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pms.optimizer.schemas import CandidateSpec, FeedbackPayload


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _number(value: float | None) -> str:
    return "未评价" if value is None else f"{value:.6f}"


def render_report(
    iteration: int, feedbacks: Sequence[FeedbackPayload], use_llm: bool = False, *,
    candidates: Mapping[str, CandidateSpec] | None = None,
    promotion_reasons: Mapping[str, str] | None = None,
) -> str:
    """Pure renderer: the caller persists Markdown and the original JSON.

    LLM polish is not configured. Even use_llm=True falls back to this report
    without making a network request. Factor names are usage, not attribution.
    """
    if iteration < 1:
        raise ValueError("iteration must be positive")
    lookup = {} if candidates is None else candidates
    reasons = {} if promotion_reasons is None else promotion_reasons
    slow_count = sum(f.oos_pnl is not None for f in feedbacks)
    lines = [f"# 第 {iteration} 轮：M5 基础复盘", "",
             "本报告用于开发流程检查；不是因果归因、真实收益证明或最终样本外评价。", "",
             f"候选：{len(feedbacks)}；有开发内慢评结果：{slow_count}；晋级：{sum(f.promoted for f in feedbacks)}。", ""]
    if use_llm:
        lines += ["LLM 润色未配置，已使用模板；模型调用数为 0。", ""]
    lines += ["| 候选 | 层级 / 父代 | 权重 / 入场门槛 | 方向准确率 | Brier | 快评综合分 | 开发内后半窗口 PnL | 状态 / 原因 |",
              "|---|---|---|---:|---:|---:|---:|---|"]
    translations = {"promoted_to_champion": "晋级本地冠军",
                    "below_fast_gate_or_not_in_top_k": "未进入慢评（未入 Top-K）",
                    "rejected_by_slow_loop_or_champion": "未晋级；详见验收/冠军结果"}
    for feedback in feedbacks:
        candidate = lookup.get(feedback.candidate_id)
        lineage = "未提供配置"
        parameters = "未提供配置"
        if candidate is not None:
            lineage = f"{candidate.proposer_level} / {candidate.parent_candidate_id or '无'}"
            parameters = ", ".join(f"{s.factor_id}={s.weight:g}" for s in candidate.steps)
            parameters += f" / edge={candidate.edge_threshold:g}"
        status = reasons.get(feedback.candidate_id,
                             translations.get(feedback.suggestion, feedback.suggestion))
        row = [feedback.candidate_id, lineage, parameters, _number(feedback.direction_accuracy),
               _number(feedback.fast_brier), _number(feedback.fast_composite),
               _number(feedback.oos_pnl), status]
        lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    counts = Counter(name for feedback in feedbacks for name in set(feedback.exercised_factors))
    lines += ["", "## 配置使用情况", "",
              *[f"- {_cell(name)}：{count} 个候选启用。" for name, count in sorted(counts.items())],
              "", "以上仅是启用因子频数，不是实际触发次数、贡献值或因果效应。", "",
              "## 基础复盘与下一轮", ""]
    if not feedbacks:
        lines.append("- 本轮没有候选，检查预算或搜索空间是否耗尽。")
    if any(f.oos_pnl is None for f in feedbacks):
        lines.append("- 未进入慢评的候选记为未评价，不能解释为零收益或慢验收失败。")
    if any(f.oos_pnl is not None and f.oos_pnl <= 0 for f in feedbacks):
        lines.append("- 部分候选开发内后半窗口收益非正；仅凭该数字不能判断是信号、成本还是样本问题。")
    if any(f.promoted for f in feedbacks):
        lines.append("- 本轮更新了本地研究冠军；没有注册或激活线上策略。")
    lines += ["- 下一轮 M1 依据开发快评选择父代，进行有限变异并保留随机探索。", "",
              "## 评价边界", "",
              "队友字段中的 oos 是开发内部切片：同一面板已经参与 M2 筛选，不能作为独立最终测试。",
              "当前 M3 是每点一份合约的简化模拟；覆盖率、门槛调用与时序隔离仍需单独完善。",
              "原系统 Evaluator 的成交与归因证据尚未接入。本报告不自动推断具体因子应增加或减少。", ""]
    return "\n".join(lines)
