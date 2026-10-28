"""Self-iterating factor-composition optimizer (M2/M3/M4 + self-hosted tracing)."""

from pms.optimizer.champion_pool import ChampionPool, ChampionRecord
from pms.optimizer.fast_loop import FastLoopEvaluator
from pms.optimizer.ic_metrics import edge_return_pairs, ic_series, pearson, spearman
from pms.optimizer.loop import CandidateResult, OptimizationLoop
from pms.optimizer.replay import OutcomeStore, ReplayPoint, replay_composition
from pms.optimizer.schemas import (
    CandidateSpec,
    CompositionStepSpec,
    FastScore,
    FeedbackPayload,
    PromotionDecision,
    SlowScore,
    WindowScore,
)
from pms.optimizer.slow_loop import SlowLoopValidator
from pms.optimizer.tracer import Span, SpanNode, Tracer

__all__ = (
    "CandidateResult",
    "CandidateSpec",
    "ChampionPool",
    "ChampionRecord",
    "CompositionStepSpec",
    "FastLoopEvaluator",
    "FastScore",
    "FeedbackPayload",
    "edge_return_pairs",
    "ic_series",
    "pearson",
    "spearman",
    "OptimizationLoop",
    "OutcomeStore",
    "PromotionDecision",
    "ReplayPoint",
    "SlowLoopValidator",
    "SlowScore",
    "Span",
    "SpanNode",
    "Tracer",
    "WindowScore",
    "replay_composition",
)
