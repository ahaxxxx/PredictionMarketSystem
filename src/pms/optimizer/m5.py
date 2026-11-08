"""M5 first version: deterministic report of the existing feedback contract."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Literal
from math import isfinite
from pms.optimizer.evidence import FeatureView, effective_values, forecasts, nonzero_rate, record_id
from pms.optimizer.research_contracts import (
    DiagnosticRecord, EditRecommendation, EvidenceRecord, ReviewBundle,
)
from pms.optimizer.schemas import CandidateSpec, FeedbackPayload


def build_review(
    iteration: int, feedbacks: Sequence[FeedbackPayload], *,
    candidates: Mapping[str, CandidateSpec], features: FeatureView,
) -> ReviewBundle:
    """Build auditable deterministic diagnostics; no LLM or extra outcome replay.

    One label-free forecast replay per candidate is counted explicitly. Costs,
    factor contributions and causal explanations are not inferred from scores.
    """
    if iteration < 1 or len({f.candidate_id for f in feedbacks}) != len(feedbacks):
        raise ValueError("positive iteration and distinct feedback required")
    fingerprint = features.fingerprint
    evidence: list[EvidenceRecord] = []
    diagnostics: list[DiagnosticRecord] = []
    recommendations: list[EditRecommendation] = []
    rates = {name: nonzero_rate(effective_values(features, name)) for name in features.factor_ids}

    def fact(candidate_id: str, target: str, metric: str, value: float, definition: str,
             n_points: int | None = None) -> EvidenceRecord:
        item = EvidenceRecord(record_id(fingerprint, iteration, candidate_id, target, metric),
                              features.dataset_id, fingerprint, iteration, candidate_id,
                              target, metric, value, n_points, definition)
        evidence.append(item)
        return item

    def advise(candidate: CandidateSpec, target: str, operation: Literal["freeze", "explore", "toward_zero"], item: EvidenceRecord,
               issue: str, observation: str, hypothesis: str) -> None:
        diagnostic_id = record_id(item.evidence_id, issue)
        diagnostics.append(DiagnosticRecord(
            diagnostic_id, candidate.candidate_id, issue, (item.evidence_id,), observation,
            hypothesis, "只描述当前搜索特征；不证明收益、因果关系或未来有效性。"))
        recommendations.append(EditRecommendation(
            record_id(diagnostic_id, operation), diagnostic_id, candidate.candidate_id,
            target, operation,
            (item.evidence_id,), "新候选若无行为变化或开发质量恶化，则不支持该修改。"))

    for feedback in feedbacks:
        candidate = candidates.get(feedback.candidate_id)
        if candidate is None or tuple(s.factor_id for s in candidate.steps) != features.factor_ids:
            raise ValueError("feedback candidate or feature schema mismatch")
        if not isfinite(feedback.fast_brier):
            raise ValueError("finite feedback required")
        fact(candidate.candidate_id, "candidate", "fast_brier", feedback.fast_brier,
             "M2 reported development Brier; not independent final-test evidence")
        predictions = forecasts(candidate, features)
        saturation = sum(p <= 1e-6 or p >= 1 - 1e-6 for p in predictions) / len(predictions)
        clipped = fact(candidate.candidate_id, "candidate", "boundary_rate", saturation,
                       "fraction of feature-only predictions at the composition probability bounds",
                       len(features.points))
        for step in candidate.steps:
            item = fact(candidate.candidate_id, step.factor_id, "effective_nonzero_rate",
                        rates[step.factor_id], "fraction with abs(rule_delta input) > 1e-12; not attribution",
                        len(features.points))
            if item.value == 0:
                advise(candidate, step.factor_id, "freeze", item, "inactive_factor",
                       "当前搜索面板中该因子的有效修正量始终为零。",
                       "本轮局部搜索可保留该权重，比较其他维度；换数据后重新判断。")
            elif saturation > 0 and step.weight != 0:
                advise(candidate, step.factor_id, "toward_zero", clipped, "prediction_boundary",
                       "部分预测到达概率边界。",
                       "减小该权重幅度，检验边界比例及开发误差是否改善。")
            else:
                advise(candidate, step.factor_id, "explore", item, "testable_parameter",
                       "该因子在搜索面板中具有非零有效输入。",
                       "可比较相邻权重；当前事实不决定权重应该为正还是为负。")
        advise(candidate, "edge_threshold", "explore", clipped, "entry_threshold",
               "已记录候选预测的边界比例。",
               "可比较入场门槛是否改变拟交易行为；收益方向仍待评价。")
    return ReviewBundle(iteration, features.dataset_id, fingerprint, tuple(evidence),
                        tuple(diagnostics), tuple(recommendations), len(feedbacks))


def render_review(review: ReviewBundle) -> str:
    lines = ["## M5 证据与下一轮建议", "",
             "本节由确定性规则生成，M5 不调用模型。建议是待检验假设。", "",
             "| 父候选 | 目标 | 操作 | 证据 |", "|---|---|---|---|"]
    for recommendation in review.recommendations:
        lines.append("| " + " | ".join(_cell(v) for v in (
            recommendation.parent_candidate_id[:12], recommendation.target,
            recommendation.operation, ", ".join(recommendation.evidence_ids))) + " |")
    for diagnostic in review.diagnostics:
        lines += ["", f"- {diagnostic.issue}：{diagnostic.observation} {diagnostic.hypothesis}"]
    lines += ["", f"附加特征回放：{review.feature_replays} 次；未进行新增收益评价。", ""]
    return "\n".join(lines)


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
              "当前 M3 是每点一份合约的简化模拟；独立事件验证集尚待接入。",
              "原系统 Evaluator 的成交与归因证据尚未接入。本报告不自动推断具体因子应增加或减少。", ""]
    return "\n".join(lines)
