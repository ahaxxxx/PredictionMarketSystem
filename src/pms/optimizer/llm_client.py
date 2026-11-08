"""Offline proposal seam and bounded audit ledger. No provider/network adapter yet.

The reference responder and scripted fixture exercise L3 wiring; neither is an
LLM. A future provider must implement ProposalBackend and report model usage.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any, Protocol

from pms.optimizer.m1 import ProposalSpace
from pms.optimizer.research_contracts import ProposalEdit, ProposalRequest


class ProposalBackend(Protocol):
    name: str
    is_real_model: bool

    def complete(self, request: ProposalRequest) -> str: ...


class ScriptedBackend:
    name = "scripted_fixture"
    is_real_model = False

    def __init__(self, responses: tuple[str, ...]) -> None:
        self._responses = iter(responses)

    def complete(self, request: ProposalRequest) -> str:
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise ValueError("scripted responses exhausted") from exc


class OfflineEvidenceBackend:
    """Deterministic reference policy to demonstrate feedback consumption only."""

    name = "offline_evidence_reference"
    is_real_model = False

    def complete(self, request: ProposalRequest) -> str:
        space = ProposalSpace(request.factor_ids, request.weights, request.edge_thresholds)
        parents = {c.candidate_id: c for c in request.parents}
        excluded = set(request.excluded_candidate_ids)
        edits = []
        for recommendation in request.review.recommendations:
            if recommendation.operation == "freeze":
                continue
            parent = parents[recommendation.parent_candidate_id]
            weights = tuple(step.weight for step in parent.steps)
            target = recommendation.target
            index = request.factor_ids.index(target) if target != "edge_threshold" else -1
            old = weights[index] if index >= 0 else parent.edge_threshold
            grid = request.weights if index >= 0 else request.edge_thresholds
            # Only adjacent changes; this baseline makes no profitability claim.
            position = grid.index(old)
            options = sorted((grid[j] for j in (position - 1, position + 1) if 0 <= j < len(grid)),
                             key=lambda value: (abs(value), value))
            for value in options:
                if recommendation.operation == "toward_zero" and abs(value) >= abs(old):
                    continue
                changed = list(weights)
                if index >= 0:
                    changed[index] = value
                child = space.candidate(tuple(changed), value if index < 0 else parent.edge_threshold)
                if child.candidate_id in excluded:
                    continue
                edits.append(ProposalEdit(
                    parent.candidate_id, target, value, recommendation.recommendation_id,
                    recommendation.evidence_ids, "increase" if value > old else "decrease",
                    "按所引用的开发证据比较一个相邻参数；预期质量改善尚未验证。"))
                excluded.add(child.candidate_id)
                break
            if len(edits) >= request.max_candidates:
                break
        return json.dumps({"proposals": [asdict(edit) for edit in edits]}, ensure_ascii=False)


class ProposalClient:
    """One bounded request per round; failed calls consume the same call budget."""

    def __init__(self, backend: ProposalBackend, *, max_calls: int = 20) -> None:
        if type(max_calls) is not int or max_calls < 0:
            raise ValueError("nonnegative integer proposal call budget required")
        self.backend = backend
        self.max_calls = max_calls
        self.events: list[dict[str, Any]] = []
        self.calls = 0

    def complete(self, request: ProposalRequest) -> str | None:
        event: dict[str, Any] = {"iteration": request.iteration, "backend": self.backend.name,
                                 "request": request.to_dict(), "response": None}
        self.events.append(event)
        if self.calls >= self.max_calls:
            event["status"] = "budget_exhausted"
            return None
        self.calls += 1
        try:
            response = self.backend.complete(request)
            if not isinstance(response, str) or len(response.encode()) > 65_536:
                raise ValueError("response must be bounded text")
        except Exception as exc:
            event.update(status="backend_error", error_type=type(exc).__name__)
            return None
        event.update(status="responded", response=response)
        return response

    def counts(self) -> dict[str, Any]:
        return {"backend": self.backend.name, "proposal_backend_calls": self.calls,
                "llm_calls": self.calls if self.backend.is_real_model else 0,
                "offline_calls": 0 if self.backend.is_real_model else self.calls,
                "call_budget": self.max_calls, "token_usage": None, "cost": None}
