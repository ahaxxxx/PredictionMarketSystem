from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from pms.optimizer.m1 import M1Proposer, ProposalSpace
from pms.optimizer.m5 import render_report
from pms.optimizer.run import DemoConfig, DemoOutcomes, run_demo, synthetic_input
from pms.optimizer.replay import replay_composition
from pms.optimizer.schemas import CandidateSpec, FeedbackPayload


def feedback(candidate: CandidateSpec, score: float = 0.5) -> FeedbackPayload:
    return FeedbackPayload(candidate.candidate_id, False, score, 0.2, 0.7, None, None, None,
                           tuple(s.factor_id for s in candidate.steps), "below_fast_gate_or_not_in_top_k")


def test_multigeneration_reproducibility_parents_and_single_coordinate_mutation() -> None:
    proposers = (M1Proposer(seed=17), M1Proposer(seed=17))
    batches = [p.propose([], 8) for p in proposers]
    assert batches[0] == batches[1]
    next_batches = [p.propose([feedback(c, index / 10) for index, c in enumerate(batch)], 8)
                    for p, batch in zip(proposers, batches, strict=True)]
    assert next_batches[0] == next_batches[1]
    lookup = {c.candidate_id: c for c in batches[0]}
    children = [c for c in next_batches[0] if c.proposer_level == "L2"]
    assert children
    for child in children:
        assert child.parent_candidate_id in lookup
        parent = lookup[child.parent_candidate_id]
        differences = sum(a.weight != b.weight for a, b in zip(child.steps, parent.steps, strict=True))
        differences += child.edge_threshold != parent.edge_threshold
        assert differences == 1
    assert not {c.candidate_id for c in batches[0]} & {c.candidate_id for c in next_batches[0]}


def test_configuration_identity_excludes_lineage_and_exhausts_without_retry_loop() -> None:
    space = ProposalSpace()
    assert space.candidate((0.0, 0.25), 0.05).candidate_id == space.candidate(
        (0.0, 0.25), 0.05, level="L2", parent="parent"
    ).candidate_id
    proposer = M1Proposer(space)
    batch = proposer.propose([], 1000)
    assert len(batch) == len({c.candidate_id for c in batch}) == 75
    assert proposer.propose([feedback(c) for c in batch], 8) == []


def test_unknown_or_nonfinite_feedback_cannot_mutate_proposer_state() -> None:
    proposer = M1Proposer()
    generated = proposer.propose([], 2)
    unknown = ProposalSpace().candidate((0.5, 0.5), 0.10)
    if unknown.candidate_id in {c.candidate_id for c in generated}:
        unknown = ProposalSpace().candidate((-0.5, -0.5), 0.10)
    with pytest.raises(ValueError, match="parent configuration"):
        proposer.propose([feedback(unknown)], 2)
    with pytest.raises(ValueError, match="nonfinite"):
        proposer.propose([replace(feedback(generated[0]), fast_composite=float("nan"))], 2)
    assert len(proposer.candidates) == 2


def test_slow_results_and_promotions_do_not_select_evolutionary_parents() -> None:
    first, second = M1Proposer(seed=7), M1Proposer(seed=7)
    a, b = first.propose([], 8), second.propose([], 8)
    ordinary = [feedback(c, i / 10) for i, c in enumerate(a)]
    altered = [replace(feedback(c, i / 10), oos_pnl=1000 - i, oos_brier=0.01,
                       promoted=True, champion_version=99) for i, c in enumerate(b)]
    assert first.propose(ordinary, 8) == second.propose(altered, 8)


def test_l2_continues_without_any_promoted_champion() -> None:
    proposer = M1Proposer()
    first = proposer.propose([], 8)
    assert any(c.proposer_level == "L2" for c in proposer.propose([feedback(c) for c in first], 8))


@pytest.mark.parametrize("factory", [
    lambda: ProposalSpace(weights=(0.0, float("inf"))), lambda: ProposalSpace(weights=(0.25, 0.5)),
    lambda: ProposalSpace(factor_ids=("not-a-factor",)), lambda: ProposalSpace(edge_thresholds=(0.0, 0.1)),
])
def test_illegal_search_spaces_rejected(factory: Callable[[], ProposalSpace]) -> None:
    with pytest.raises(ValueError):
        factory()


def test_template_report_keeps_missing_slow_results_and_does_not_invent_attribution() -> None:
    candidate = ProposalSpace().candidate((0.25, 0.0), 0.05)
    report = render_report(1, [feedback(candidate)], use_llm=True,
                           candidates={candidate.candidate_id: candidate})
    assert "未评价" in report and "模型调用数为 0" in report
    assert "不是实际触发次数、贡献值或因果效应" in report
    assert "不是因果归因" in report
    assert "独立最终测试" in report


def test_report_uses_actual_promotion_reason_and_escapes_table_cells() -> None:
    candidate = ProposalSpace().candidate((0.0, 0.25), 0.05)
    report = render_report(1, [feedback(candidate)],
                           promotion_reasons={candidate.candidate_id: "incumbent | stronger\nthan candidate"})
    assert "incumbent \\| stronger than candidate" in report
    assert render_report(1, []).endswith("\n")


def test_three_round_demo_runs_real_m2_m3_m4_and_saves_reports(tmp_path: Path) -> None:
    result = run_demo(tmp_path, DemoConfig(markets=16))
    assert result["rounds"] == 3
    assert result["candidate_evaluations"] == 24
    assert result["proposer_levels"]["L2"] > 0
    assert result["llm_calls"] == 0 and result["registered"] is False
    assert result["final_holdout_used"] is False
    registry = json.loads((tmp_path / "candidate_registry.json").read_text())
    assert len(registry) == 24
    for number in range(1, 4):
        counts = json.loads((tmp_path / "iterations" / f"{number:03d}" / "counts.json").read_text())
        assert counts["fast_evaluated"] == 8 and counts["slow_evaluated"] == 3
        assert (tmp_path / "reports" / f"iteration-{number:03d}.md").exists()
    with closing(sqlite3.connect(tmp_path / "champions.sqlite3")) as connection:
        assert connection.execute("SELECT count(*) FROM candidates").fetchone()[0] == 24
    with pytest.raises(ValueError, match="empty output"):
        run_demo(tmp_path)


def test_demo_space_exhaustion_stops_cleanly(tmp_path: Path) -> None:
    result = run_demo(tmp_path, DemoConfig(rounds=20, batch_size=50, top_k=1, markets=8))
    assert result["candidate_evaluations"] == 75
    assert result["rounds"] == 2
    assert result["stop_reason"] == "space_exhausted"


def test_synthetic_features_do_not_depend_on_sampled_outcome() -> None:
    config = DemoConfig(markets=8)
    panel, outcomes, data = synthetic_input(config)
    assert len(panel) == len(outcomes.values) == 8
    assert data["kind"] == "synthetic_development_demo"
    candidate = ProposalSpace().candidate((0.25, 0.5), 0.05)
    original = replay_composition(candidate, panel, outcomes)
    flipped = replay_composition(candidate, panel, DemoOutcomes(tuple((key, 1-value) for key, value in outcomes.values)))
    assert [p.forecast for p in original] == [p.forecast for p in flipped]
    assert [p.actual for p in original] != [p.actual for p in flipped]


def test_failed_core_run_is_recorded_not_reported_as_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("synthetic injected failure")
    monkeypatch.setattr("pms.optimizer.run.OptimizationLoop.run_once", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        run_demo(tmp_path, DemoConfig(markets=8))
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "failed"
