"""M1: bounded random/evolution proposals using the shared CandidateSpec.

The instance owns parent configurations; FeedbackPayload alone cannot recover
them. No LLM, external I/O, strategy registration or runtime activation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json
from math import ceil, isfinite
import random

from pms.factors.composition import validate_composition
from pms.optimizer.schemas import CandidateSpec, CompositionStepSpec, FeedbackPayload


@dataclass(frozen=True, slots=True)
class ProposalSpace:
    factor_ids: tuple[str, ...] = ("orderbook_imbalance", "metaculus_prior")
    weights: tuple[float, ...] = (-0.5, -0.25, 0.0, 0.25, 0.5)
    edge_thresholds: tuple[float, ...] = (0.02, 0.05, 0.10)

    def __post_init__(self) -> None:
        allowed = {"orderbook_imbalance", "metaculus_prior", "favorite_longshot_bias"}
        if not self.factor_ids or len(set(self.factor_ids)) != len(self.factor_ids):
            raise ValueError("distinct factor dimensions required")
        if not set(self.factor_ids) <= allowed:
            raise ValueError("first version supports only declared raw rule_delta factors")
        if not self.weights or tuple(sorted(set(self.weights))) != self.weights:
            raise ValueError("weights must be nonempty, ordered and unique")
        if 0.0 not in self.weights or any(not isfinite(w) or abs(w) > 2 for w in self.weights):
            raise ValueError("finite bounded weights including zero required")
        if (not self.edge_thresholds or tuple(sorted(set(self.edge_thresholds))) != self.edge_thresholds
                or any(not isfinite(v) or not 0 < v < 1 for v in self.edge_thresholds)):
            raise ValueError("ordered unique edge thresholds inside (0,1) required")
        if self.size > 100_000:
            raise ValueError("finite first-version space capped at 100000")

    @property
    def size(self) -> int:
        size = len(self.edge_thresholds)
        for _ in self.factor_ids:
            size *= len(self.weights)
        return size

    def candidate(
        self, weights: tuple[float, ...], edge: float, *, level: str = "L1", parent: str | None = None,
    ) -> CandidateSpec:
        if len(weights) != len(self.factor_ids) or any(w not in self.weights for w in weights):
            raise ValueError("weights outside declared space")
        if edge not in self.edge_thresholds:
            raise ValueError("edge outside declared space")
        steps = tuple(CompositionStepSpec(name, "rule_delta", weight=0.0 if w == 0 else w,
                                          required=True, allow_neutral_fallback=False)
                      for name, w in zip(self.factor_ids, weights, strict=True))
        genotype = {"steps": [step.to_dict() for step in steps], "edge_threshold": edge}
        identity = sha256(json.dumps(genotype, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()
        candidate = CandidateSpec(identity, steps, edge, level, parent)
        validate_composition(candidate.composition_steps())
        return candidate

    def pool(self) -> tuple[CandidateSpec, ...]:
        return tuple(self.candidate(tuple(ws), edge)
                     for ws in product(self.weights, repeat=len(self.factor_ids))
                     for edge in self.edge_thresholds)


class M1Proposer:
    """Keep one instance per fixed dataset/protocol; no module-global state."""

    def __init__(self, space: ProposalSpace = ProposalSpace(), *, seed: int = 42) -> None:
        self.space = space
        self._pool = space.pool()
        self._rng = random.Random(seed)
        self._registry: dict[str, CandidateSpec] = {}
        self._scores: dict[str, FeedbackPayload] = {}

    @property
    def candidates(self) -> tuple[CandidateSpec, ...]:
        return tuple(self._registry.values())

    def _neighbors(self, parent: CandidateSpec) -> list[CandidateSpec]:
        weights = tuple(step.weight for step in parent.steps)
        neighbors = []
        for dimension, value in enumerate(weights):
            index = self.space.weights.index(value)
            for position in (index - 1, index + 1):
                if 0 <= position < len(self.space.weights):
                    mutated = list(weights)
                    mutated[dimension] = self.space.weights[position]
                    neighbors.append(self.space.candidate(tuple(mutated), parent.edge_threshold,
                                                          level="L2", parent=parent.candidate_id))
        index = self.space.edge_thresholds.index(parent.edge_threshold)
        for position in (index - 1, index + 1):
            if 0 <= position < len(self.space.edge_thresholds):
                neighbors.append(self.space.candidate(weights, self.space.edge_thresholds[position],
                                                      level="L2", parent=parent.candidate_id))
        return neighbors

    def propose(
        self, feedbacks: Sequence[FeedbackPayload], n: int, rng: random.Random | None = None,
    ) -> list[CandidateSpec]:
        if type(n) is not int or n < 0:
            raise ValueError("n must be a nonnegative integer")
        # Validate the whole feedback batch before changing instance state.
        for feedback in feedbacks:
            if feedback.candidate_id not in self._registry:
                raise ValueError("feedback has no parent configuration in this proposer")
            if not all(isfinite(v) for v in (feedback.fast_composite, feedback.fast_brier,
                                            feedback.direction_accuracy)):
                raise ValueError("nonfinite feedback")
        self._scores.update((f.candidate_id, f) for f in feedbacks)
        generator = self._rng if rng is None else rng
        remaining = {c.candidate_id: c for c in self._pool if c.candidate_id not in self._registry}
        count = min(n, len(remaining))
        chosen: list[CandidateSpec] = []

        def take(candidate: CandidateSpec) -> None:
            chosen.append(candidate)
            remaining.pop(candidate.candidate_id)

        if count and not self._registry:
            take(self.space.candidate((0.0,) * len(self.space.factor_ids), self.space.edge_thresholds[0]))
        ranked = sorted(self._scores.values(), key=lambda f: (-f.fast_composite, f.fast_brier, f.candidate_id))
        # Only development fast scores select parents. Slow/OOS and promotion
        # fields are intentionally not used as an evolutionary fitness signal.
        parents = [self._registry[f.candidate_id] for f in ranked[:3]]
        mutation_budget = min(count, ceil(count * 0.75)) if parents else 0
        while len(chosen) < mutation_budget:
            possibilities = [c for parent in parents for c in self._neighbors(parent)
                             if c.candidate_id in remaining]
            if not possibilities:
                break
            take(generator.choice(possibilities))
        while len(chosen) < count:
            take(generator.choice(list(remaining.values())))
        self._registry.update((candidate.candidate_id, candidate) for candidate in chosen)
        return chosen
