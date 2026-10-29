"""M2 — fast-loop evaluator.

Scores a candidate composition by replaying it over the historical factor
panel with no trading simulation: two objectives, directional accuracy and
calibration (Brier / log loss).  The IC / RankIC / ICIR information
coefficients from formulaic alpha-factor mining (AlphaBench) are reported
alongside as additive diagnostics — the original objectives and gates are
unchanged.  Designed to evaluate one candidate in seconds, so hundreds of
proposals per loop iteration are affordable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pms.research.cache import FactorPanel

from pms.optimizer.ic_metrics import ic_series
from pms.optimizer.replay import OutcomeStore, replay_composition
from pms.optimizer.schemas import CandidateSpec, FastScore

__all__ = ("FastLoopEvaluator",)


@dataclass(frozen=True, slots=True)
class FastLoopEvaluator:
    """Dual-objective panel-replay scorer with a hard time budget."""

    budget_s: float = 5.0
    min_coverage: float = 0.5

    def evaluate(
        self,
        candidate: CandidateSpec,
        panel: FactorPanel,
        outcomes: OutcomeStore,
    ) -> FastScore:
        deadline = time.monotonic() + self.budget_s
        started = time.monotonic()

        points = replay_composition(candidate, panel, outcomes)
        scored: list[tuple[bool, float, float]] = []
        for point in points:
            if time.monotonic() > deadline:
                break
            scored.append(
                (
                    point.direction_correct,
                    point.brier(),
                    point.log_loss(),
                )
            )

        n_scored = len(scored)
        # Denominator counts scorable (market, timestamp) pairs, not raw
        # factor rows: one timestamp may carry several factor rows, and a
        # fully scored timestamp must read as full coverage.
        n_resolvable_points = sum(
            len({row.ts for row in rows})
            for market_id, rows in panel.items()
            if outcomes.outcome(market_id) is not None
        )
        if n_scored == 0:
            return FastScore(
                direction_accuracy=0.0,
                brier=1.0,
                log_loss=_LN2,
                coverage=0.0,
                n_points=0,
                n_markets=len(panel),
                duration_s=time.monotonic() - started,
            )

        correct = sum(1 for ok, _, _ in scored if ok)
        brier = sum(b for _, b, _ in scored) / n_scored
        log_loss = sum(ll for _, _, ll in scored) / n_scored
        coverage = n_scored / max(1, n_resolvable_points)
        ic = ic_series(points)
        return FastScore(
            direction_accuracy=correct / n_scored,
            brier=brier,
            log_loss=log_loss,
            coverage=coverage,
            n_points=n_scored,
            n_markets=len(panel),
            duration_s=time.monotonic() - started,
            ic=float(ic["ic"]),
            rank_ic=float(ic["rank_ic"]),
            icir=float(ic["icir"]),
        )

    def passes_gate(self, score: FastScore) -> bool:
        """Cheap admission gate before the expensive slow loop runs."""
        return score.coverage >= self.min_coverage and score.brier < 0.25


_LN2 = 0.6931471805599453
