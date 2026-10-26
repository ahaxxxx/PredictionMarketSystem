"""Replay a candidate composition over a historical factor panel.

A ``FactorPanel`` (see ``pms.research.cache``) maps ``market_id`` to a sorted
tuple of ``FactorValueRow``.  For each timestamp we materialise the
``(factor_id, param) -> value`` mapping that ``apply_composition`` expects,
resolve the candidate's composed probability, and join the market outcome
(1.0 = YES, 0.0 = NO).  The result is the flat list of replay points that
both M2 (fast loop) and M3 (slow loop) score.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pms.factors.base import FactorValueRow
from pms.factors.composition import apply_composition
from pms.research.cache import FactorPanel

from pms.optimizer.schemas import CandidateSpec

__all__ = ("OutcomeStore", "ReplayPoint", "replay_composition")


class OutcomeStore(Protocol):
    """Binary market resolution lookup: 1.0 = YES, 0.0 = NO, None = unresolved."""

    def outcome(self, market_id: str) -> float | None: ...


@dataclass(frozen=True, slots=True)
class ReplayPoint:
    market_id: str
    ts: datetime
    forecast: float
    actual: float
    yes_price: float | None

    @property
    def direction_correct(self) -> bool:
        predicted_yes = self.forecast >= 0.5
        actual_yes = self.actual >= 0.5
        return predicted_yes == actual_yes

    def brier(self) -> float:
        return (self.forecast - self.actual) ** 2

    def log_loss(self) -> float:
        p = min(1.0 - 1e-9, max(1e-9, self.forecast))
        return -(self.actual * _ln(p) + (1.0 - self.actual) * _ln(1.0 - p))


def replay_composition(
    candidate: CandidateSpec,
    panel: FactorPanel,
    outcomes: OutcomeStore,
) -> tuple[ReplayPoint, ...]:
    """Evaluate the candidate at every panel timestamp of every market.

    Timestamps whose market outcome is unknown are skipped (they cannot be
    scored); composition failures at a single timestamp are also skipped so one
    bad row never sinks a whole candidate — the coverage metric exposes how
    much of the panel was actually scored.
    """
    steps = candidate.composition_steps()
    points: list[ReplayPoint] = []
    for market_id, rows in panel.items():
        actual = outcomes.outcome(market_id)
        if actual is None or not rows:
            continue
        values_by_ts = _values_by_timestamp(rows)
        for ts in sorted(values_by_ts):
            factor_values = values_by_ts[ts]
            yes_row = factor_values.get(("yes_price", ""))
            try:
                forecast = apply_composition(steps, factor_values)
            except (KeyError, ValueError):
                continue
            points.append(
                ReplayPoint(
                    market_id=market_id,
                    ts=ts,
                    forecast=forecast,
                    actual=actual,
                    yes_price=yes_row,
                )
            )
    return tuple(points)


def _values_by_timestamp(
    rows: tuple[FactorValueRow, ...],
) -> dict[datetime, dict[tuple[str, str], float]]:
    by_ts: dict[datetime, dict[tuple[str, str], float]] = {}
    for row in rows:
        by_ts.setdefault(row.ts, {})[(row.factor_id, row.param)] = row.value
    return by_ts


def _ln(value: float) -> float:
    import math

    return math.log(value)
