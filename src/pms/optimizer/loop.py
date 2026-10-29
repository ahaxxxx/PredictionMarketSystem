"""Closed-loop driver: M2 → top-K → M3 → M4 → feedback payload.

One ``run_once`` call is one iteration of the self-improving loop:

1. Score every M1-proposed candidate with the fast loop (M2), each under
   its own trace span.
2. Admit the top ``top_k`` candidates by fast composite into the slow loop
   (M3) for a fee/slippage walk-forward backtest.
3. Hand each validated candidate to the champion pool (M4), which promotes
   only if the gates pass and the reigning champion is beaten.
4. Emit a deterministic ``FeedbackPayload`` per candidate — M5 turns it
   into a report, M1 consumes it to propose the next generation.

The loop never requires an LLM: if M1's L3 proposer is absent, feed it the
payloads from the previous iteration and let L1/L2 mutate from there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pms.research.cache import FactorPanel

from pms.optimizer.champion_pool import ChampionPool
from pms.optimizer.fast_loop import FastLoopEvaluator
from pms.optimizer.replay import OutcomeStore
from pms.optimizer.schemas import (
    CandidateSpec,
    FastScore,
    FeedbackPayload,
    PromotionDecision,
    SlowScore,
)
from pms.optimizer.slow_loop import SlowLoopValidator
from pms.optimizer.tracer import Tracer

__all__ = ("CandidateResult", "OptimizationLoop")


@dataclass(frozen=True, slots=True)
class CandidateResult:
    candidate: CandidateSpec
    fast: FastScore
    slow: SlowScore | None
    promotion: PromotionDecision | None
    feedback: FeedbackPayload


class OptimizationLoop:
    """Wires M2/M3/M4 together and traces every step locally."""

    def __init__(
        self,
        *,
        fast_evaluator: FastLoopEvaluator,
        slow_validator: SlowLoopValidator,
        champion_pool: ChampionPool,
        tracer: Tracer,
        top_k: int = 3,
    ) -> None:
        if top_k < 1:
            msg = "top_k must be >= 1"
            raise ValueError(msg)
        self._fast = fast_evaluator
        self._slow = slow_validator
        self._pool = champion_pool
        self._tracer = tracer
        self._top_k = top_k

    def run_once(
        self,
        candidates: Sequence[CandidateSpec],
        panel: FactorPanel,
        outcomes: OutcomeStore,
    ) -> list[CandidateResult]:
        with self._tracer.trace(
            "optimizer_iteration",
            inputs={"n_candidates": len(candidates)},
        ) as iteration:
            fast_scores = self._score_all(candidates, panel, outcomes)
            gated = {
                candidate.candidate_id
                for candidate in candidates
                if self._fast.passes_gate(fast_scores[candidate.candidate_id])
            }
            admitted = self._select_top_k(candidates, fast_scores) & gated
            results: list[CandidateResult] = []
            for candidate in candidates:
                fast = fast_scores[candidate.candidate_id]
                if candidate.candidate_id not in gated:
                    reason = (
                        f"fast gate: coverage {fast.coverage:.3f} / "
                        f"brier {fast.brier:.4f}"
                    )
                    results.append(
                        CandidateResult(
                            candidate=candidate,
                            fast=fast,
                            slow=None,
                            promotion=PromotionDecision(False, None, reason),
                            feedback=self._feedback(candidate, fast, None, None),
                        )
                    )
                    continue
                if candidate.candidate_id not in admitted:
                    results.append(
                        CandidateResult(
                            candidate=candidate,
                            fast=fast,
                            slow=None,
                            promotion=None,
                            feedback=self._feedback(candidate, fast, None, None),
                        )
                    )
                    continue
                slow = self._validate(candidate, panel, outcomes)
                if not self._slow.passes_gate(slow):
                    promotion = PromotionDecision(
                        False,
                        None,
                        "slow gate: out-of-sample trades or pnl below requirement",
                    )
                    self._pool.record_score(candidate.candidate_id, "slow", slow)
                else:
                    promotion = self._promote(candidate, fast, slow)
                results.append(
                    CandidateResult(
                        candidate=candidate,
                        fast=fast,
                        slow=slow,
                        promotion=promotion,
                        feedback=self._feedback(candidate, fast, slow, promotion),
                    )
                )
            iteration.set_outputs(
                evaluated=len(candidates),
                admitted=len(admitted),
                promoted=sum(
                    1 for r in results if r.promotion and r.promotion.promoted
                ),
            )
            return results

    # ---- stages -------------------------------------------------------------

    def _score_all(
        self,
        candidates: Sequence[CandidateSpec],
        panel: FactorPanel,
        outcomes: OutcomeStore,
    ) -> dict[str, FastScore]:
        scores: dict[str, FastScore] = {}
        for candidate in candidates:
            self._pool.record_candidate(candidate)
            with self._tracer.trace(
                "fast_evaluate",
                inputs={"candidate_id": candidate.candidate_id},
            ) as span:
                score = self._fast.evaluate(candidate, panel, outcomes)
                self._pool.record_score(candidate.candidate_id, "fast", score)
                span.set_outputs(**score.to_dict())
                scores[candidate.candidate_id] = score
        return scores

    def _select_top_k(
        self,
        candidates: Sequence[CandidateSpec],
        fast_scores: dict[str, FastScore],
    ) -> set[str]:
        ranked = sorted(
            candidates,
            key=lambda c: fast_scores[c.candidate_id].composite(),
            reverse=True,
        )
        return {c.candidate_id for c in ranked[: self._top_k]}

    def _validate(
        self,
        candidate: CandidateSpec,
        panel: FactorPanel,
        outcomes: OutcomeStore,
    ) -> SlowScore:
        with self._tracer.trace(
            "slow_validate",
            inputs={"candidate_id": candidate.candidate_id},
        ) as span:
            slow = self._slow.validate(candidate, panel, outcomes)
            self._pool.record_score(candidate.candidate_id, "slow", slow)
            span.set_outputs(total_pnl=slow.total_pnl, oos_pnl=slow.oos_pnl())
        return slow

    def _promote(
        self,
        candidate: CandidateSpec,
        fast: FastScore,
        slow: SlowScore,
    ) -> PromotionDecision:
        with self._tracer.trace(
            "promote",
            inputs={"candidate_id": candidate.candidate_id},
        ) as span:
            decision = self._pool.promote(candidate, fast, slow)
            span.set_outputs(**decision.to_dict())
        return decision

    # ---- feedback -----------------------------------------------------------

    def _feedback(
        self,
        candidate: CandidateSpec,
        fast: FastScore,
        slow: SlowScore | None,
        promotion: PromotionDecision | None,
    ) -> FeedbackPayload:
        exercised = tuple(
            sorted({step.factor_id for step in candidate.steps if step.enabled})
        )
        if promotion is not None and promotion.promoted:
            suggestion = "promoted_to_champion"
        elif slow is None:
            suggestion = "below_fast_gate_or_not_in_top_k"
        else:
            suggestion = "rejected_by_slow_loop_or_champion"
        return FeedbackPayload(
            candidate_id=candidate.candidate_id,
            promoted=bool(promotion and promotion.promoted),
            fast_composite=fast.composite(),
            fast_brier=fast.brier,
            direction_accuracy=fast.direction_accuracy,
            oos_pnl=slow.oos_pnl() if slow else None,
            oos_brier=slow.oos_brier() if slow else None,
            champion_version=promotion.champion_version if promotion else None,
            exercised_factors=exercised,
            suggestion=suggestion,
            ic=fast.ic,
            rank_ic=fast.rank_ic,
            icir=fast.icir,
        )
