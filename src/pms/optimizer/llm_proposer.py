"""Compile evidence-linked single-parameter edits; reject safely and fall back to L2."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
import json
from math import isfinite
from typing import Any, cast

from pms.optimizer.evidence import FeatureView, behavior_changed
from pms.optimizer.llm_client import ProposalClient
from pms.optimizer.m1 import M1Proposer
from pms.optimizer.research_contracts import ProposalEdit, ProposalRequest, ReviewBundle
from pms.optimizer.schemas import CandidateSpec, FeedbackPayload


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("nonempty bounded text required")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate JSON keys")
    return dict(pairs)


def parse_edits(response: str, limit: int) -> tuple[ProposalEdit, ...]:
    raw = _object(json.loads(response, object_pairs_hook=_unique_object))
    items = raw.get("proposals")
    if set(raw) != {"proposals"} or not isinstance(items, list) or len(items) > limit:
        raise ValueError("bounded proposals array required")
    result = []
    for item in items:
        row = _object(item)
        required = {"parent_candidate_id", "target", "value", "recommendation_id",
                    "evidence_ids", "expected_direction", "hypothesis"}
        if set(row) != required:
            raise ValueError("proposal fields differ from the edit contract")
        value = row["value"]
        if type(value) not in (int, float) or not isfinite(cast(float, value)):
            raise ValueError("finite numeric parameter required")
        references = row["evidence_ids"]
        if not isinstance(references, list) or not references or len(references) > 8:
            raise ValueError("nonempty bounded evidence references required")
        direction = row["expected_direction"]
        if direction not in ("increase", "decrease"):
            raise ValueError("parameter direction must be increase or decrease")
        result.append(ProposalEdit(
            _text(row["parent_candidate_id"]), _text(row["target"]), float(cast(float, value)),
            _text(row["recommendation_id"]), tuple(_text(ref) for ref in references),
            direction, _text(row["hypothesis"])))
    return tuple(result)


class GuidedProposer:
    def __init__(self, base: M1Proposer, features: FeatureView,
                 client: ProposalClient | None = None) -> None:
        self.base = base
        self.features = features
        self.client = client
        self.attempts: list[dict[str, Any]] = []
        self.feature_checks = 0

    def propose(self, feedbacks: Sequence[FeedbackPayload], n: int, *, iteration: int,
                review: ReviewBundle | None = None) -> list[CandidateSpec]:
        if type(n) is not int or n < 0:
            raise ValueError("nonnegative integer candidate budget required")
        if self.client and review is not None and (review.iteration != iteration - 1 or review.feature_sha256 != self.features.fingerprint
                or review.scope != "search_development_only"):
            raise ValueError("stale or foreign M5 review")
        self.base.observe(feedbacks)
        if not self.client or review is None or n == 0:
            return self.base.propose([], n)
        remaining = self.base.space.size - len(self.base.candidates)
        if not remaining:
            return []
        # Reserve at least one L1/L2 slot; rejected L3 edits never add hidden evaluations.
        quota = min(remaining, n - max(1, n // 4))
        if quota == 0:
            return self.base.propose([], n)
        request = ProposalRequest(iteration, quota, self.base.space.factor_ids,
                                  self.base.space.weights, self.base.space.edge_thresholds,
                                  self.base.candidates, tuple(c.candidate_id for c in self.base.candidates), review)
        response = self.client.complete(request)
        chosen: list[CandidateSpec] = []
        if response is None:
            self.attempts.append({"iteration": iteration, "status": "fallback",
                                  "reason": self.client.events[-1]["status"]})
        else:
            try:
                edits = parse_edits(response, quota)
            except (ValueError, TypeError, OverflowError):
                self.attempts.append({"iteration": iteration, "status": "rejected",
                                      "reason": "invalid_response_contract", "response": response})
                edits = ()
            for index, edit in enumerate(edits):
                attempt: dict[str, Any] = {"iteration": iteration, "proposal_index": index,
                                           "edit": asdict(edit)}
                self.attempts.append(attempt)
                try:
                    candidate = self._compile(edit, review)
                    self.base.register_candidates([candidate])
                except ValueError as exc:
                    attempt.update(status="rejected", reason=str(exc))
                    continue
                chosen.append(candidate)
                attempt.update(status="accepted", candidate_id=candidate.candidate_id)
        chosen.extend(self.base.propose([], n - len(chosen)))
        return chosen

    def _compile(self, edit: ProposalEdit, review: ReviewBundle) -> CandidateSpec:
        recommendations = {r.recommendation_id: r for r in review.recommendations}
        recommendation = recommendations.get(edit.recommendation_id)
        if (recommendation is None or recommendation.parent_candidate_id != edit.parent_candidate_id
                or recommendation.target != edit.target):
            raise ValueError("recommendation does not match parent and target")
        evidence = {e.evidence_id: e for e in review.evidence}
        if (set(edit.evidence_ids) != set(recommendation.evidence_ids)
                or any(ref not in evidence for ref in edit.evidence_ids)):
            raise ValueError("unknown or mismatched evidence reference")
        if recommendation.operation == "freeze":
            raise ValueError("inactive target is frozen for this local edit")
        parents = {c.candidate_id: c for c in self.base.candidates}
        parent = parents.get(edit.parent_candidate_id)
        if parent is None:
            raise ValueError("unknown parent")
        weights = [s.weight for s in parent.steps]
        edge = parent.edge_threshold
        if edit.target == "edge_threshold":
            old, edge = edge, edit.value
        elif edit.target in self.base.space.factor_ids:
            index = self.base.space.factor_ids.index(edit.target)
            old, weights[index] = weights[index], edit.value
        else:
            raise ValueError("unknown edit target")
        if (edit.value == old or (edit.value > old) != (edit.expected_direction == "increase")):
            raise ValueError("declared parameter direction disagrees with actual edit")
        if recommendation.operation == "toward_zero" and abs(edit.value) >= abs(old):
            raise ValueError("edit does not move parameter toward zero")
        child = self.base.space.candidate(tuple(weights), edge, level="L3", parent=parent.candidate_id)
        if child.candidate_id in parents:
            raise ValueError("duplicate candidate configuration")
        self.feature_checks += 1
        if not behavior_changed(parent, child, self.features):
            raise ValueError("edit has no observable forecast or intended-trade change")
        return child
