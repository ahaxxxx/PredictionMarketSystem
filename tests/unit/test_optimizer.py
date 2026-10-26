"""Unit tests for the self-iterating optimizer (M2/M3/M4 + self-hosted tracer)."""

from __future__ import annotations

from datetime import datetime, timedelta

from pms.factors.base import FactorValueRow
from pms.optimizer import (
    CandidateSpec,
    ChampionPool,
    CompositionStepSpec,
    FastLoopEvaluator,
    OptimizationLoop,
    SlowLoopValidator,
    Tracer,
    replay_composition,
)
from pms.research.cache import FactorPanel

BASE_TS = datetime(2026, 1, 1, 0, 0, 0)


def _row(market_id: str, idx: int, factor_id: str, value: float, param: str = "") -> FactorValueRow:
    return FactorValueRow(
        factor_id=factor_id,
        param=param,
        market_id=market_id,
        ts=BASE_TS + timedelta(hours=idx),
        value=value,
    )


class _Outcomes:
    def __init__(self, mapping: dict[str, float]) -> None:
        self._mapping = mapping

    def outcome(self, market_id: str) -> float | None:
        return self._mapping.get(market_id)


def _oracle_panel() -> FactorPanel:
    """Two resolved markets, 8 hourly timestamps each.

    Market m1 resolves YES: oracle factor reads 0.8, yes_price drifts 0.4→0.6.
    Market m2 resolves NO:  oracle factor reads 0.2, yes_price drifts 0.6→0.4.
    Market m3 is unresolved and must be skipped entirely.
    """
    panel: dict[str, tuple[FactorValueRow, ...]] = {}
    rows_m1 = []
    rows_m2 = []
    for idx in range(8):
        price1 = 0.4 + idx * 0.02
        rows_m1.append(_row("m1", idx, "yes_price", price1))
        rows_m1.append(_row("m1", idx, "oracle", 0.8))
        price2 = 0.6 - idx * 0.02
        rows_m2.append(_row("m2", idx, "yes_price", price2))
        rows_m2.append(_row("m2", idx, "oracle", 0.2))
    rows_m3 = [_row("m3", idx, "oracle", 0.5) for idx in range(8)]
    panel["m1"] = tuple(rows_m1)
    panel["m2"] = tuple(rows_m2)
    panel["m3"] = tuple(rows_m3)
    return panel


OUTCOMES = _Outcomes({"m1": 1.0, "m2": 0.0})


def _candidate(candidate_id: str, oracle_value: float | None = None) -> CandidateSpec:
    steps = [
        CompositionStepSpec(
            factor_id="oracle",
            role="weighted",
            weight=1.0,
        )
    ]
    return CandidateSpec(candidate_id=candidate_id, steps=tuple(steps), edge_threshold=0.1)


# ---- tracer -----------------------------------------------------------------


def test_tracer_nests_and_flushes_spans() -> None:
    with Tracer.in_memory() as tracer:
        with tracer.trace("iteration", inputs={"n": 2}) as root:
            with tracer.trace("fast_evaluate") as child:
                child.set_outputs(brier=0.1)
        assert tracer.count() == 2
        tree = tracer.get_trace(root.span_id)
        assert tree is not None
        assert tree.name == "iteration"
        assert len(tree.children) == 1
        assert tree.children[0].outputs["brier"] == 0.1
        assert tree.children[0].duration_s is not None
        roots = tracer.roots(name="iteration")
        assert len(roots) == 1


def test_tracer_records_errors() -> None:
    with Tracer.in_memory() as tracer:
        try:
            with tracer.trace("boom"):
                raise RuntimeError("kaboom")
        except RuntimeError:
            pass
        roots = tracer.roots()
        assert roots[0].status == "error"
        assert "kaboom" in roots[0].outputs["error"]


# ---- schemas ----------------------------------------------------------------


def test_candidate_spec_json_round_trip() -> None:
    candidate = _candidate("c-1")
    restored = CandidateSpec.from_json(candidate.to_json())
    assert restored == candidate
    assert restored.steps[0].factor_id == "oracle"


# ---- replay -----------------------------------------------------------------


def test_replay_skips_unresolved_and_scores_resolved() -> None:
    points = replay_composition(_candidate("c-1"), _oracle_panel(), OUTCOMES)
    # 2 resolved markets x 8 timestamps; m3 skipped.
    assert len(points) == 16
    m1_points = [p for p in points if p.market_id == "m1"]
    assert all(p.forecast == 0.8 for p in m1_points)
    assert all(p.direction_correct for p in points)


# ---- M2 fast loop ------------------------------------------------------------


def test_fast_loop_dual_objective_score() -> None:
    score = FastLoopEvaluator().evaluate(_candidate("c-1"), _oracle_panel(), OUTCOMES)
    assert score.direction_accuracy == 1.0
    # forecast 0.8 vs actual 1.0 / 0.0 -> brier = 0.04 both ways
    assert abs(score.brier - 0.04) < 1e-9
    assert score.n_points == 16
    assert score.coverage > 0.0
    assert score.composite() > 0.0
    assert FastLoopEvaluator().passes_gate(score)


def test_fast_loop_empty_panel_scores_zero() -> None:
    score = FastLoopEvaluator().evaluate(_candidate("c-1"), {}, OUTCOMES)
    assert score.n_points == 0
    assert score.coverage == 0.0
    assert not FastLoopEvaluator().passes_gate(score)


# ---- M3 slow loop ------------------------------------------------------------


def test_slow_loop_walk_forward_windows_and_fees() -> None:
    validator = SlowLoopValidator(fee_bps=0.0, slippage_bps=0.0, n_windows=4)
    score = validator.validate(_candidate("c-1"), _oracle_panel(), OUTCOMES)
    assert len(score.windows) == 4
    in_sample = score.in_sample
    out_of_sample = score.out_of_sample
    assert len(in_sample) == 2
    assert len(out_of_sample) == 2
    # oracle has real edge at every timestamp -> positive pnl with zero fees
    assert score.total_pnl > 0.0
    assert score.max_drawdown <= 0.0
    assert validator.passes_gate(score)


def test_slow_loop_fees_can_turn_pnl_negative() -> None:
    validator = SlowLoopValidator(fee_bps=8000.0, slippage_bps=0.0, n_windows=4)
    score = validator.validate(_candidate("c-1"), _oracle_panel(), OUTCOMES)
    assert score.total_pnl < 0.0
    assert not validator.passes_gate(score)


def test_slow_loop_no_trades_when_edge_below_threshold() -> None:
    high_edge = CandidateSpec(
        candidate_id="c-tight",
        steps=_candidate("c-tight").steps,
        edge_threshold=0.9,
    )
    score = SlowLoopValidator(n_windows=2).validate(
        high_edge, _oracle_panel(), OUTCOMES
    )
    assert all(w.n_trades == 0 for w in score.windows)


# ---- M4 champion pool ---------------------------------------------------------


def _good_fast_slow():
    fast = FastLoopEvaluator().evaluate(_candidate("c-1"), _oracle_panel(), OUTCOMES)
    slow = SlowLoopValidator(fee_bps=0.0, slippage_bps=0.0).validate(
        _candidate("c-1"), _oracle_panel(), OUTCOMES
    )
    return fast, slow


def test_champion_pool_promotes_first_champion_and_versions() -> None:
    with ChampionPool.in_memory() as pool:
        fast, slow = _good_fast_slow()
        decision = pool.promote(_candidate("c-1"), fast, slow)
        assert decision.promoted
        assert decision.champion_version == 1
        champion = pool.current_champion()
        assert champion is not None
        assert champion.candidate_id == "c-1"
        exported = pool.export_champion_spec()
        assert exported is not None
        assert CandidateSpec.from_json(exported).candidate_id == "c-1"


def test_champion_pool_rejects_weaker_challenger() -> None:
    with ChampionPool.in_memory() as pool:
        fast, slow = _good_fast_slow()
        assert pool.promote(_candidate("c-1"), fast, slow).promoted
        # identical candidate can never beat the incumbent composite
        decision = pool.promote(_candidate("c-2"), fast, slow)
        assert not decision.promoted
        assert "incumbent" in decision.reason
        assert pool.current_champion() is not None
        assert pool.current_champion().version == 1
        assert len(pool.ledger()) == 1


def test_champion_pool_rejects_bad_fast_score() -> None:
    with ChampionPool.in_memory() as pool:
        _, slow = _good_fast_slow()
        bad_fast = FastLoopEvaluator().evaluate(
            _candidate("c-bad"), {}, OUTCOMES
        )
        decision = pool.promote(_candidate("c-bad"), bad_fast, slow)
        assert not decision.promoted
        assert decision.reason == "fast brier above gate"


# ---- end-to-end loop -----------------------------------------------------------


def test_loop_end_to_end_promotes_best_candidate() -> None:
    with Tracer.in_memory() as tracer, ChampionPool.in_memory() as pool:
        _run_loop_assertions(tracer, pool)


def _run_loop_assertions(tracer: Tracer, pool: ChampionPool) -> None:
    loop = OptimizationLoop(
        fast_evaluator=FastLoopEvaluator(),
        slow_validator=SlowLoopValidator(fee_bps=0.0, slippage_bps=0.0),
        champion_pool=pool,
        tracer=tracer,
        top_k=2,
    )
    strong = _candidate("c-strong")
    # "weak" = market-price follower: forecast == yes_price, zero edge,
    # direction accuracy well below the oracle follower.
    weak = CandidateSpec(
        candidate_id="c-weak",
        steps=(
            CompositionStepSpec(factor_id="yes_price", role="weighted", weight=1.0),
        ),
        edge_threshold=0.1,
    )
    results = loop.run_once([strong, weak], _oracle_panel(), OUTCOMES)
    assert len(results) == 2
    by_id = {r.candidate.candidate_id: r for r in results}
    strong_result = by_id["c-strong"]
    assert strong_result.slow is not None
    assert strong_result.promotion is not None
    assert strong_result.promotion.promoted
    assert strong_result.feedback.promoted
    assert strong_result.feedback.champion_version == 1
    # weak candidate gets slow-scored (top_k=2) but its zero-edge trading
    # cannot beat the reigning champion.
    weak_result = by_id["c-weak"]
    assert weak_result.slow is not None
    assert weak_result.promotion is not None
    assert not weak_result.promotion.promoted
    assert weak_result.feedback.suggestion == "rejected_by_slow_loop_or_champion"
    # every stage left a trace under one iteration root
    roots = tracer.roots(name="optimizer_iteration")
    assert len(roots) == 1
    names = {child.name for child in roots[0].children}
    assert "fast_evaluate" in names
    assert "slow_validate" in names
    assert "promote" in names
