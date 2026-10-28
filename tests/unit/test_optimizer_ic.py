"""Unit tests for the AlphaBench-style IC metrics mixed into M2."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pms.factors.base import FactorValueRow
from pms.optimizer import (
    CandidateSpec,
    CompositionStepSpec,
    FastLoopEvaluator,
    FastScore,
    edge_return_pairs,
    ic_series,
    pearson,
    spearman,
)
from pms.optimizer.replay import ReplayPoint

BASE_TS = datetime(2026, 1, 1, 0, 0, 0)


def _point(idx: int, forecast: float, price: float, actual: float, market: str = "m") -> ReplayPoint:
    return ReplayPoint(
        market_id=market,
        ts=BASE_TS + timedelta(hours=idx),
        forecast=forecast,
        actual=actual,
        yes_price=price,
    )


class _Outcomes:
    def __init__(self, mapping: dict[str, float]) -> None:
        self._mapping = mapping

    def outcome(self, market_id: str) -> float | None:
        return self._mapping.get(market_id)


# ---- primitive correlations ----------------------------------------------


def test_pearson_perfect_and_anti_correlation() -> None:
    xs = (1.0, 2.0, 3.0, 4.0)
    assert pearson(xs, (2.0, 4.0, 6.0, 8.0)) > 0.999
    assert pearson(xs, (8.0, 6.0, 4.0, 2.0)) < -0.999


def test_pearson_degenerate_variance_is_zero() -> None:
    assert pearson((1.0, 1.0, 1.0), (2.0, 1.0, 4.0)) == 0.0
    assert pearson((1.0, 2.0), (3.0,)) == 0.0


def test_spearman_with_ties() -> None:
    xs = (1.0, 1.0, 2.0, 3.0)
    ys = (5.0, 5.0, 2.0, 1.0)
    # xs and ys ranks are perfectly anti-monotone despite ties -> -1.
    assert spearman(xs, ys) == pytest.approx(-1.0)
    assert spearman((1.0, 2.0, 3.0), (10.0, 20.0, 30.0)) > 0.999


# ---- replay-point level metrics -------------------------------------------


def test_ic_perfect_factor_scores_one() -> None:
    # forecast fixed at 0.8; price falls over time, actual = 1: both the
    # edge (forecast - price) and the realized return (actual - price) rise
    # together -> perfect positive IC.
    points = tuple(
        _point(i, 0.8, 0.6 - i * 0.02, 1.0) for i in range(8)
    )
    ic = ic_series(points)
    assert ic["ic"] > 0.99
    assert ic["rank_ic"] > 0.99


def test_ic_anti_factor_scores_negative_one() -> None:
    # forecast tracks the price (zero information): edge constant -> IC 0.
    points = tuple(
        _point(i, 0.5 - i * 0.02, 0.5 - i * 0.02, 1.0) for i in range(8)
    )
    ic = ic_series(points)
    assert ic["ic"] == 0.0


def test_edge_return_pairs_skip_missing_prices() -> None:
    points = (
        _point(0, 0.7, 0.5, 1.0),
        ReplayPoint("m", BASE_TS, 0.7, 1.0, None),
    )
    edges, realized = edge_return_pairs(points)
    assert len(edges) == 1
    assert edges[0] == pytest.approx(0.2)
    assert realized[0] == pytest.approx(0.5)


# ---- M2 integration ----------------------------------------------------------


def _priced_panel() -> dict[str, tuple[FactorValueRow, ...]]:
    """Two YES-markets whose prices drift down: an oracle factor with a
    constant 0.8 forecast has edges that perfectly rank realized returns."""
    panel = {}
    for market in ("p1", "p2"):
        rows = []
        for i in range(8):
            ts = BASE_TS + timedelta(hours=i)
            rows.append(FactorValueRow("yes_price", "", market, ts, 0.6 - i * 0.02))
            rows.append(FactorValueRow("oracle", "", market, ts, 0.8))
        panel[market] = tuple(rows)
    return panel


def test_fast_loop_reports_ic_metrics() -> None:
    candidate = CandidateSpec(
        candidate_id="ic-1",
        steps=(CompositionStepSpec(factor_id="oracle", role="weighted", weight=1.0),),
    )
    score = FastLoopEvaluator().evaluate(
        candidate, _priced_panel(), _Outcomes({"p1": 1.0, "p2": 1.0})
    )
    assert score.ic > 0.99
    assert score.rank_ic > 0.99
    # original objectives untouched
    assert score.direction_accuracy == 1.0
    assert abs(score.brier - 0.04) < 1e-9
    assert score.composite() > 0.0


def test_fast_score_json_round_trip_keeps_ic_fields() -> None:
    score = FastScore(
        direction_accuracy=0.7,
        brier=0.2,
        log_loss=0.5,
        coverage=0.9,
        n_points=10,
        n_markets=2,
        duration_s=0.1,
        ic=0.31,
        rank_ic=0.29,
        icir=1.2,
    )
    restored = FastScore.from_dict(score.to_dict())
    assert restored == score
    assert restored.icir == 1.2


def test_old_scores_without_ic_fields_deserialize() -> None:
    legacy = {
        "direction_accuracy": 0.7,
        "brier": 0.2,
        "log_loss": 0.5,
        "coverage": 0.9,
        "n_points": 10,
        "n_markets": 2,
        "duration_s": 0.1,
    }
    score = FastScore.from_dict(legacy)
    assert score.ic == 0.0
    assert score.rank_ic == 0.0
    assert score.icir == 0.0
