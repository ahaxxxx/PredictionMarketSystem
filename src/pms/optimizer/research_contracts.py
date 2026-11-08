"""M1/M5 sidecar records; the teammate's CandidateSpec contract stays unchanged."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from pms.optimizer.schemas import CandidateSpec


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    dataset_id: str
    feature_sha256: str
    iteration: int
    candidate_id: str
    target: str
    metric: str
    value: float
    n_points: int | None
    definition: str
    scope: str = "search_development_only"


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    diagnostic_id: str
    candidate_id: str
    issue: str
    evidence_ids: tuple[str, ...]
    observation: str
    hypothesis: str
    uncertainty: str


@dataclass(frozen=True, slots=True)
class EditRecommendation:
    recommendation_id: str
    diagnostic_id: str
    parent_candidate_id: str
    target: str
    operation: Literal["freeze", "explore", "toward_zero"]
    evidence_ids: tuple[str, ...]
    falsification: str


@dataclass(frozen=True, slots=True)
class ReviewBundle:
    iteration: int
    dataset_id: str
    feature_sha256: str
    evidence: tuple[EvidenceRecord, ...]
    diagnostics: tuple[DiagnosticRecord, ...]
    recommendations: tuple[EditRecommendation, ...]
    feature_replays: int
    scope: str = "search_development_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProposalRequest:
    iteration: int
    max_candidates: int
    factor_ids: tuple[str, ...]
    weights: tuple[float, ...]
    edge_thresholds: tuple[float, ...]
    parents: tuple[CandidateSpec, ...]
    excluded_candidate_ids: tuple[str, ...]
    review: ReviewBundle

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProposalEdit:
    parent_candidate_id: str
    target: str
    value: float
    recommendation_id: str
    evidence_ids: tuple[str, ...]
    expected_direction: Literal["increase", "decrease"]
    hypothesis: str
