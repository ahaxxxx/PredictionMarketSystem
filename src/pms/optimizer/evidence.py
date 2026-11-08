"""Label-free feature evidence. This module never receives an OutcomeStore.

These are descriptive checks of supplied development features, not causal
attribution. Market IDs stay in local fingerprints, never in proposal prompts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite

from pms.factors.catalog import catalog_entry
from pms.factors.composition import apply_composition
from pms.optimizer.schemas import CandidateSpec
from pms.research.cache import FactorPanel


def record_id(*parts: object) -> str:
    return sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FeaturePoint:
    market_id: str
    timestamp: str
    values: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class FeatureView:
    dataset_id: str
    role: str
    factor_ids: tuple[str, ...]
    points: tuple[FeaturePoint, ...]

    def __post_init__(self) -> None:
        if self.role != "search" or not self.dataset_id or not self.points:
            raise ValueError("M1/M5 evidence requires a nonempty search feature view; no holdouts")
        if not self.factor_ids or len(set(self.factor_ids)) != len(self.factor_ids):
            raise ValueError("distinct factor IDs required")
        expected = {"yes_price", *self.factor_ids}
        seen: set[tuple[str, str]] = set()
        for point in self.points:
            values = dict(point.values)
            key = (point.market_id, point.timestamp)
            if (key in seen or len(values) != len(point.values) or set(values) != expected
                    or any(not isfinite(v) for v in values.values())
                    or not 0 < values["yes_price"] < 1):
                raise ValueError("unique complete finite feature points required")
            seen.add(key)

    @property
    def fingerprint(self) -> str:
        return record_id(self.dataset_id, self.role, self.factor_ids,
                         [(p.market_id, p.timestamp, p.values) for p in self.points])

    @classmethod
    def from_panel(cls, panel: FactorPanel, factor_ids: tuple[str, ...], *,
                   dataset_id: str, role: str = "search") -> FeatureView:
        points: list[FeaturePoint] = []
        for market, rows in sorted(panel.items()):
            by_time: dict[str, dict[str, float]] = {}
            for row in rows:
                if row.factor_id not in {"yes_price", *factor_ids}:
                    continue
                if row.param:
                    raise ValueError("first evidence version requires canonical YES-oriented factors")
                values = by_time.setdefault(row.ts.isoformat(), {})
                if row.factor_id in values:
                    raise ValueError("duplicate feature value")
                values[row.factor_id] = row.value
            points.extend(FeaturePoint(market, ts, tuple(sorted(values.items())))
                          for ts, values in sorted(by_time.items()))
        return cls(dataset_id, role, factor_ids, tuple(points))


def effective_values(view: FeatureView, factor_id: str) -> tuple[float, ...]:
    """Probability factors contribute (prior - market), not raw prior."""
    if factor_id not in view.factor_ids:
        raise ValueError("unknown feature")
    entry = catalog_entry(factor_id)
    if entry is None:
        raise ValueError("unknown catalog factor")
    probability = entry.output_type == "probability"
    return tuple(dict(p.values)[factor_id] - (dict(p.values)["yes_price"] if probability else 0)
                 for p in view.points)


def forecasts(candidate: CandidateSpec, view: FeatureView) -> tuple[float, ...]:
    return tuple(apply_composition(candidate.composition_steps(),
                                   {(name, ""): value for name, value in point.values})
                 for point in view.points)


def behavior(candidate: CandidateSpec, view: FeatureView) -> tuple[tuple[float, int], ...]:
    """Forecast plus intended unit-trade side; no outcome or profitability."""
    result = []
    for prediction, point in zip(forecasts(candidate, view), view.points, strict=True):
        edge = prediction - dict(point.values)["yes_price"]
        side = (1 if edge >= 0 else -1) if abs(edge) >= candidate.edge_threshold else 0
        result.append((prediction, side))
    return tuple(result)


def behavior_changed(parent: CandidateSpec, child: CandidateSpec, view: FeatureView) -> bool:
    return behavior(parent, view) != behavior(child, view)


def nonzero_rate(values: Sequence[float]) -> float:
    return sum(abs(value) > 1e-12 for value in values) / len(values)
