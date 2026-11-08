"""Behavioral witnesses for M5 -> M1, safe fallback and development-only scope."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import pytest

from pms.factors.base import FactorValueRow
from pms.optimizer.evidence import FeatureView, effective_values, forecasts
from pms.optimizer.llm_client import OfflineEvidenceBackend, ProposalClient, ScriptedBackend
from pms.optimizer.llm_proposer import GuidedProposer, parse_edits
from pms.optimizer.m1 import M1Proposer, ProposalSpace
from pms.optimizer.m5 import build_review
from pms.optimizer.research_contracts import ProposalRequest, ReviewBundle
from pms.optimizer.run import DemoConfig, run_demo
from pms.optimizer.schemas import CandidateSpec, FeedbackPayload


def _feedback(candidate: CandidateSpec) -> FeedbackPayload:
    return FeedbackPayload(candidate.candidate_id, False, 0.3, 0.2, 0.5, None, None, None,
                           tuple(s.factor_id for s in candidate.steps), "development")


def _context() -> tuple[M1Proposer, FeatureView, ReviewBundle, CandidateSpec]:
    space = ProposalSpace(factor_ids=("orderbook_imbalance", "favorite_longshot_bias"))
    parent = space.candidate((0.5, 0.25), 0.05)
    base = M1Proposer(space, seed=3)
    base.register_candidates([parent])
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    rows = tuple(FactorValueRow(name, "", "anonymous", ts + timedelta(hours=i), value)
                 for i in range(4) for name, value in (
                     ("yes_price", 0.3 + 0.1 * i), ("orderbook_imbalance", 0.02 + 0.1 * i),
                     ("favorite_longshot_bias", 0.0)))
    features = FeatureView.from_panel({"anonymous": rows}, space.factor_ids, dataset_id="fixture")
    review = build_review(1, [_feedback(parent)], candidates={parent.candidate_id: parent}, features=features)
    return base, features, review, parent


def _edit(review: ReviewBundle, *, target: str = "orderbook_imbalance", value: float = 0.25) -> dict[str, Any]:
    recommendation = next(r for r in review.recommendations if r.target == target)
    return {"parent_candidate_id": recommendation.parent_candidate_id, "target": target,
            "value": value, "recommendation_id": recommendation.recommendation_id,
            "evidence_ids": list(recommendation.evidence_ids), "expected_direction": "decrease",
            "hypothesis": "比较一个权重修改，是否改善开发质量尚未验证。"}


def _guided(base: M1Proposer, features: FeatureView, edits: list[dict[str, Any]]) -> GuidedProposer:
    response = json.dumps({"proposals": edits})
    return GuidedProposer(base, features, ProposalClient(ScriptedBackend((response,))))


def test_m5_detects_effective_inactivity_without_inventing_contribution() -> None:
    _, _, review, _ = _context()
    item = next(e for e in review.evidence if e.target == "favorite_longshot_bias")
    assert item.value == 0 and item.n_points == 4
    assert next(r for r in review.recommendations if r.target == item.target).operation == "freeze"
    assert next(e for e in review.evidence if e.metric == "fast_brier").n_points is None
    assert review.feature_replays == 1


def test_probability_factor_activity_uses_prior_minus_price() -> None:
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    view = FeatureView.from_panel({"m": (
        FactorValueRow("yes_price", "", "m", ts, 0.6),
        FactorValueRow("metaculus_prior", "", "m", ts, 0.6),
    )}, ("metaculus_prior",), dataset_id="prior")
    assert effective_values(view, "metaculus_prior") == (0.0,)


def test_m5_ignores_slow_labels_and_does_not_turn_missing_scores_into_zero() -> None:
    _, view, review, parent = _context()
    changed = replace(_feedback(parent), oos_pnl=9999, oos_brier=0.0, promoted=True)
    assert build_review(1, [changed], candidates={parent.candidate_id: parent}, features=view) == review


def test_valid_m5_edit_is_compiled_registered_and_can_become_l2_parent() -> None:
    base, view, review, parent = _context()
    guided = _guided(base, view, [_edit(review)])
    batch = guided.propose([_feedback(parent)], 4, iteration=2, review=review)
    child = next(c for c in batch if c.proposer_level == "L3")
    assert child.parent_candidate_id == parent.candidate_id and child.steps[0].weight == 0.25
    assert forecasts(child, view) != forecasts(parent, view)
    assert guided.attempts[0]["status"] == "accepted"
    # Adoption is the important seam: a plain L2 proposer now accepts L3 feedback.
    next_batch = base.propose([replace(_feedback(child), fast_composite=10.0)], 3)
    assert any(c.parent_candidate_id == child.candidate_id for c in next_batch)


@pytest.mark.parametrize("problem", ["fake_evidence", "wrong_direction", "off_grid", "freeze", "unknown_parent"])
def test_invalid_edits_are_rejected_without_breaking_l2(problem: str) -> None:
    base, view, review, parent = _context()
    edit = _edit(review)
    if problem == "fake_evidence":
        edit["evidence_ids"] = ["fabricated"]
    elif problem == "wrong_direction":
        edit["expected_direction"] = "increase"
    elif problem == "off_grid":
        edit["value"] = 0.123
    elif problem == "unknown_parent":
        edit["parent_candidate_id"] = "unregistered"
    else:
        edit = _edit(review, target="favorite_longshot_bias", value=0.0)
    guided = _guided(base, view, [edit])
    batch = guided.propose([_feedback(parent)], 4, iteration=2, review=review)
    assert len(batch) == 4 and all(c.proposer_level != "L3" for c in batch)
    assert guided.attempts[0]["status"] == "rejected"
    assert len({c.candidate_id for c in base.candidates}) == 5


def test_edge_edit_that_changes_no_behavior_is_rejected() -> None:
    base, view, review, parent = _context()
    guided = _guided(base, view, [_edit(review, target="edge_threshold", value=0.02)])
    guided.propose([_feedback(parent)], 2, iteration=2, review=review)
    assert guided.attempts[0]["status"] == "rejected"
    assert "no observable" in guided.attempts[0]["reason"]


def test_duplicate_edits_do_not_create_extra_candidate_evaluations() -> None:
    base, view, review, parent = _context()
    edit = _edit(review)
    guided = _guided(base, view, [edit, edit])
    batch = guided.propose([_feedback(parent)], 4, iteration=2, review=review)
    assert len(batch) == len({c.candidate_id for c in batch}) == 4
    assert [a["status"] for a in guided.attempts] == ["accepted", "rejected"]


def test_external_registration_is_atomic_and_rejects_forged_identity() -> None:
    base, _, _, parent = _context()
    first = base.space.candidate((0.25, 0.25), 0.05, level="L3", parent=parent.candidate_id)
    second = replace(base.space.candidate((0.0, 0.25), 0.05), candidate_id="forged")
    with pytest.raises(ValueError, match="canonical"):
        base.register_candidates([first, second])
    assert base.candidates == (parent,)


@pytest.mark.parametrize("response", ["not json", '{"proposals": [], "proposals": []}',
                                       '{"proposals": [{}]}', '{"proposals": null}'])
def test_malformed_response_consumes_one_attempt_then_falls_back(response: str) -> None:
    base, view, review, parent = _context()
    client = ProposalClient(ScriptedBackend((response,)))
    guided = GuidedProposer(base, view, client)
    batch = guided.propose([_feedback(parent)], 4, iteration=2, review=review)
    assert len(batch) == 4 and client.calls == 1
    assert client.counts()["llm_calls"] == 0
    assert guided.attempts[0]["reason"] == "invalid_response_contract"


def test_budget_exhaustion_and_backend_error_fall_back_without_paid_calls() -> None:
    for budget, responses, reason in ((0, (), "budget_exhausted"), (1, (), "backend_error")):
        base, view, review, parent = _context()
        client = ProposalClient(ScriptedBackend(responses), max_calls=budget)
        guided = GuidedProposer(base, view, client)
        assert len(guided.propose([_feedback(parent)], 2, iteration=2, review=review)) == 2
        assert client.calls == budget
        assert guided.attempts[0]["reason"] == reason


def test_m5_recommendation_selection_changes_next_l3_configuration() -> None:
    children = []
    for target in ("orderbook_imbalance", "edge_threshold"):
        base, view, review, parent = _context()
        # Controlled feedback ablation: keep the same facts, select one recommendation.
        selected = replace(review, recommendations=tuple(r for r in review.recommendations if r.target == target))
        request = ProposalRequest(2, 1, base.space.factor_ids, base.space.weights,
                                  base.space.edge_thresholds, base.candidates,
                                  (parent.candidate_id,), selected)
        edits = parse_edits(OfflineEvidenceBackend().complete(request), 1)
        assert len(edits) == 1 and edits[0].target == target
        # For edge, select the other neighbor, which changes the intended trade set.
        edit = _edit(selected, target=target, value=0.1 if target == "edge_threshold" else 0.25)
        if target == "edge_threshold":
            edit["expected_direction"] = "increase"
        guided = _guided(base, view, [edit])
        children.append(next(c for c in guided.propose([_feedback(parent)], 2, iteration=2, review=selected)
                             if c.proposer_level == "L3"))
    assert children[0].candidate_id != children[1].candidate_id


def test_feature_scope_staleness_and_future_prefix_invariance() -> None:
    base, view, review, parent = _context()
    with pytest.raises(ValueError, match="no holdouts"):
        replace(view, role="final_test")
    with pytest.raises(ValueError, match="stale"):
        _guided(base, view, [_edit(review)]).propose([_feedback(parent)], 2, iteration=3, review=review)
    shorter = replace(view, points=view.points[:2])
    assert forecasts(parent, shorter) == forecasts(parent, view)[:2]


def test_three_round_offline_loop_preserves_audits_and_truthful_counts(tmp_path: Path) -> None:
    config = DemoConfig(markets=16, proposal_mode="offline-evidence")
    result = run_demo(tmp_path, config)
    assert result["rounds"] == 3 and result["candidate_evaluations"] == 24
    assert result["accepted_guided_edits"] > 0
    assert result["offline_calls"] == 2 and result["llm_calls"] == 0
    assert result["independent_validation_used"] is False and result["final_holdout_used"] is False
    calls = json.loads((tmp_path / "proposal_calls.json").read_text(encoding="utf-8"))
    assert [call["request"]["review"]["iteration"] for call in calls] == [1, 2]
    assert all("outcome" not in json.dumps(call["request"]) for call in calls)
    assert all((tmp_path / "iterations" / f"{i:03d}" / "diagnostics.json").exists() for i in (1, 2, 3))


def test_same_seed_reproduces_offline_candidates_and_evidence(tmp_path: Path) -> None:
    config = DemoConfig(markets=8, proposal_mode="offline-evidence")
    for name in ("a", "b"):
        run_demo(tmp_path / name, config)
    for artifact in ("candidate_registry.json", "proposal_attempts.json", "iterations/002/diagnostics.json"):
        assert (tmp_path / "a" / artifact).read_bytes() == (tmp_path / "b" / artifact).read_bytes()
