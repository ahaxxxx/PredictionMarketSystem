"""Shared data contracts for the self-iterating factor-composition optimizer.

This module is the interface boundary between team members:

* M1 (proposer, 博湛) produces ``CandidateSpec`` and consumes ``FeedbackPayload``.
* M2/M3/M4 (evaluation core, 蓝友成) consume ``CandidateSpec`` and produce
  ``FastScore`` / ``SlowScore`` / ``PromotionDecision``.

Everything here is plain immutable data with JSON round-trips so the contract
can be frozen independently of any implementation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from pms.factors.composition import FactorCompositionStep

__all__ = (
    "CandidateSpec",
    "CompositionStepSpec",
    "FastScore",
    "FeedbackPayload",
    "PromotionDecision",
    "SlowScore",
    "WindowScore",
)


@dataclass(frozen=True, slots=True)
class CompositionStepSpec:
    """Plain-data version of one composition step (the search genome)."""

    factor_id: str
    role: str
    weight: float = 1.0
    param: str = ""
    threshold: float | None = None
    required: bool = False
    freshness_sla_s: float | None = None
    allow_neutral_fallback: bool = False
    enabled: bool = True

    def to_step(self) -> "_StepAdapter":
        return _StepAdapter(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompositionStepSpec":
        return cls(
            factor_id=str(data["factor_id"]),
            role=str(data["role"]),
            weight=float(data.get("weight", 1.0)),
            param=str(data.get("param", "")),
            threshold=(
                None if data.get("threshold") is None else float(data["threshold"])
            ),
            required=bool(data.get("required", False)),
            freshness_sla_s=(
                None
                if data.get("freshness_sla_s") is None
                else float(data["freshness_sla_s"])
            ),
            allow_neutral_fallback=bool(data.get("allow_neutral_fallback", False)),
            enabled=bool(data.get("enabled", True)),
        )


class _StepAdapter:
    """Adapts a ``CompositionStepSpec`` to the ``FactorCompositionStep`` protocol."""

    def __init__(self, spec: CompositionStepSpec) -> None:
        self._spec = spec

    @property
    def factor_id(self) -> str:
        return self._spec.factor_id

    @property
    def role(self) -> str:
        return self._spec.role

    @property
    def param(self) -> str:
        return self._spec.param

    @property
    def weight(self) -> float:
        return self._spec.weight

    @property
    def threshold(self) -> float | None:
        return self._spec.threshold

    @property
    def required(self) -> bool:
        return self._spec.required

    @property
    def freshness_sla_s(self) -> float | None:
        return self._spec.freshness_sla_s

    @property
    def allow_neutral_fallback(self) -> bool:
        return self._spec.allow_neutral_fallback

    @property
    def enabled(self) -> bool:
        return self._spec.enabled


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    """A proposed factor composition plus searchable scalar knobs."""

    candidate_id: str
    steps: tuple[CompositionStepSpec, ...]
    edge_threshold: float = 0.05
    proposer_level: str = "L1"
    parent_candidate_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "steps": [step.to_dict() for step in self.steps],
            "edge_threshold": self.edge_threshold,
            "proposer_level": self.proposer_level,
            "parent_candidate_id": self.parent_candidate_id,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateSpec":
        return cls(
            candidate_id=str(data["candidate_id"]),
            steps=tuple(
                CompositionStepSpec.from_dict(step)
                for step in data.get("steps", ())
            ),
            edge_threshold=float(data.get("edge_threshold", 0.05)),
            proposer_level=str(data.get("proposer_level", "L1")),
            parent_candidate_id=data.get("parent_candidate_id"),
            meta=dict(data.get("meta", {})),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "CandidateSpec":
        return cls.from_dict(json.loads(payload))

    def composition_steps(self) -> tuple[FactorCompositionStep, ...]:
        return tuple(step.to_step() for step in self.steps)


@dataclass(frozen=True, slots=True)
class FastScore:
    """M2 result: dual-objective replay score over the factor panel.

    The direction/calibration objectives are the original design.  The
    ``ic`` / ``rank_ic`` / ``icir`` fields follow the standard formulaic
    alpha-factor mining metrics (AlphaBench; Grinold & Kahn 2000), where
    IC measures the correlation between the predicted edge and the realized
    return of trading on it.  They are additive — nothing in the original
    scoring changed.
    """

    direction_accuracy: float
    brier: float
    log_loss: float
    coverage: float
    n_points: int
    n_markets: int
    duration_s: float
    ic: float = 0.0
    rank_ic: float = 0.0
    icir: float = 0.0

    def composite(self) -> float:
        """Higher is better: accuracy minus calibration penalty."""
        return self.direction_accuracy - self.brier

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FastScore":
        return cls(
            direction_accuracy=float(data["direction_accuracy"]),
            brier=float(data["brier"]),
            log_loss=float(data["log_loss"]),
            coverage=float(data["coverage"]),
            n_points=int(data["n_points"]),
            n_markets=int(data["n_markets"]),
            duration_s=float(data["duration_s"]),
            ic=float(data.get("ic", 0.0)),
            rank_ic=float(data.get("rank_ic", 0.0)),
            icir=float(data.get("icir", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class WindowScore:
    """Backtest result for one walk-forward window."""

    window_index: int
    ts_start: str
    ts_end: str
    in_sample: bool
    n_trades: int
    pnl: float
    hit_rate: float
    brier: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WindowScore":
        return cls(
            window_index=int(data["window_index"]),
            ts_start=str(data["ts_start"]),
            ts_end=str(data["ts_end"]),
            in_sample=bool(data["in_sample"]),
            n_trades=int(data["n_trades"]),
            pnl=float(data["pnl"]),
            hit_rate=float(data["hit_rate"]),
            brier=float(data["brier"]),
        )


@dataclass(frozen=True, slots=True)
class SlowScore:
    """M3 result: fee/slippage backtest with walk-forward out-of-sample."""

    windows: tuple[WindowScore, ...]
    total_pnl: float
    max_drawdown: float
    pnl_per_trade: float
    fee_bps: float
    slippage_bps: float

    @property
    def out_of_sample(self) -> tuple[WindowScore, ...]:
        return tuple(window for window in self.windows if not window.in_sample)

    @property
    def in_sample(self) -> tuple[WindowScore, ...]:
        return tuple(window for window in self.windows if window.in_sample)

    def oos_pnl(self) -> float:
        return sum(window.pnl for window in self.out_of_sample)

    def oos_brier(self) -> float | None:
        oos = self.out_of_sample
        if not oos:
            return None
        total_points = sum(max(1, window.n_trades) for window in oos)
        if total_points == 0:
            return None
        return sum(window.brier * max(1, window.n_trades) for window in oos) / total_points

    def to_dict(self) -> dict[str, Any]:
        return {
            "windows": [window.to_dict() for window in self.windows],
            "total_pnl": self.total_pnl,
            "max_drawdown": self.max_drawdown,
            "pnl_per_trade": self.pnl_per_trade,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SlowScore":
        return cls(
            windows=tuple(WindowScore.from_dict(w) for w in data.get("windows", ())),
            total_pnl=float(data["total_pnl"]),
            max_drawdown=float(data["max_drawdown"]),
            pnl_per_trade=float(data["pnl_per_trade"]),
            fee_bps=float(data["fee_bps"]),
            slippage_bps=float(data["slippage_bps"]),
        )


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """M4 result: whether the candidate became the new champion."""

    promoted: bool
    champion_version: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FeedbackPayload:
    """Structured feedback handed back to M1 (proposer) via M5.

    M5 turns this into a natural-language / LLM-friendly report; the payload
    itself stays deterministic so the loop never depends on an LLM.
    """

    candidate_id: str
    promoted: bool
    fast_composite: float
    fast_brier: float
    direction_accuracy: float
    oos_pnl: float | None
    oos_brier: float | None
    champion_version: int | None
    # Per-step contribution hints: which weighted factors got exercised.
    exercised_factors: tuple[str, ...]
    suggestion: str
    # AlphaBench-style diagnostics (additive; default 0 for legacy payloads).
    ic: float = 0.0
    rank_ic: float = 0.0
    icir: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "promoted": self.promoted,
            "fast_composite": self.fast_composite,
            "fast_brier": self.fast_brier,
            "direction_accuracy": self.direction_accuracy,
            "oos_pnl": self.oos_pnl,
            "oos_brier": self.oos_brier,
            "champion_version": self.champion_version,
            "exercised_factors": list(self.exercised_factors),
            "suggestion": self.suggestion,
            "ic": self.ic,
            "rank_ic": self.rank_ic,
            "icir": self.icir,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
