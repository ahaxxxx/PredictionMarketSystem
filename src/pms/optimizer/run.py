"""Run M1--M5 with baseline or explicitly offline evidence-guided proposals."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import random
from typing import Any

from pms.factors.base import FactorValueRow
from pms.optimizer.champion_pool import ChampionPool
from pms.optimizer.data import load_historical_input
from pms.optimizer.fast_loop import FastLoopEvaluator
from pms.optimizer.evidence import FeatureView
from pms.optimizer.llm_client import OfflineEvidenceBackend, ProposalBackend, ProposalClient, ScriptedBackend
from pms.optimizer.llm_proposer import GuidedProposer
from pms.optimizer.loop import OptimizationLoop
from pms.optimizer.m1 import M1Proposer, ProposalSpace
from pms.optimizer.m5 import build_review, render_report, render_review
from pms.optimizer.research_contracts import ReviewBundle
from pms.optimizer.schemas import FeedbackPayload
from pms.optimizer.replay import OutcomeStore
from pms.optimizer.slow_loop import SlowLoopValidator
from pms.optimizer.tracer import Tracer
from pms.research.cache import FactorPanel


@dataclass(frozen=True, slots=True)
class DemoConfig:
    rounds: int = 3
    batch_size: int = 8
    top_k: int = 3
    seed: int = 42
    markets: int = 64
    fee_bps: float = 25.0
    slippage_bps: float = 10.0
    proposal_mode: str = "baseline"
    proposal_call_budget: int = 20

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in (self.rounds, self.batch_size, self.top_k, self.seed,
                                           self.markets, self.proposal_call_budget)):
            raise ValueError("integer budgets required")
        if self.proposal_mode not in {"baseline", "offline-evidence", "scripted"} or self.proposal_call_budget < 0:
            raise ValueError("invalid proposal mode or call budget")
        if self.rounds < 1 or self.batch_size < 1 or not 1 <= self.top_k <= self.batch_size:
            raise ValueError("invalid rounds, batch size or top-k")
        if self.markets < 8:
            raise ValueError("at least eight synthetic markets required")
        if any(not isfinite(v) or v < 0 for v in (self.fee_bps, self.slippage_bps)):
            raise ValueError("finite nonnegative synthetic costs required")


@dataclass(frozen=True, slots=True)
class DemoOutcomes:
    values: tuple[tuple[str, float], ...]

    def outcome(self, market_id: str) -> float | None:
        return next((value for key, value in self.values if key == market_id), None)


def _write(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def synthetic_input(config: DemoConfig) -> tuple[FactorPanel, DemoOutcomes, dict[str, object]]:
    """Features precede a separately sampled outcome. No outcome feeds a feature."""
    rng = random.Random(config.seed + 1009)
    panel: dict[str, tuple[FactorValueRow, ...]] = {}
    outcomes: list[tuple[str, float]] = []
    rows: list[dict[str, object]] = []
    for index in range(config.markets):
        market = f"synthetic-{index:04d}"
        observed = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index)
        latent = rng.uniform(0.15, 0.85)
        market_probability = max(0.05, min(0.95, latent + rng.uniform(-0.15, 0.15)))
        prior = max(0.01, min(0.99, latent + rng.uniform(-0.1, 0.1)))
        imbalance = max(-1.0, min(1.0, 2 * (latent - market_probability) + rng.uniform(-0.2, 0.2)))
        outcome = float(rng.random() < latent)
        features = (("yes_price", market_probability), ("metaculus_prior", prior),
                    ("orderbook_imbalance", imbalance))
        panel[market] = tuple(FactorValueRow(name, "", market, observed, value)
                              for name, value in features)
        outcomes.append((market, outcome))
        rows.append({"market_id": market, "observed_at": observed.isoformat(),
                     "available_at": observed.isoformat(), "factors": dict(features),
                     "outcome": outcome,
                     "outcome_available_at": (observed + timedelta(days=1)).isoformat()})
    return panel, DemoOutcomes(tuple(outcomes)), {
        "kind": "synthetic_development_demo", "rows": rows,
        "as_of": (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=config.markets + 1)).isoformat(),
        "note": "Retrospective development-only smoke data; not real exchange evidence.",
    }


def run_demo(output: Path, config: DemoConfig = DemoConfig(), *,
             backend: ProposalBackend | None = None) -> dict[str, Any]:
    """Exercise the real teammate modules; no test doubles or external services.

    The whole supplied panel is development data. Existing M3 'OOS' fields
    remain development slices and never represent an independent final test.
    """
    panel, outcomes, input_data = synthetic_input(config)
    return _run_input(output, config, panel, outcomes, input_data, ProposalSpace(),
                      {"kind": "synthetic_development_demo"}, synthetic=True, backend=backend)


def run_historical(input_path: Path, output: Path, config: DemoConfig = DemoConfig(), *,
                   backend: ProposalBackend | None = None) -> dict[str, Any]:
    prepared = load_historical_input(input_path)
    return _run_input(output, config, prepared.panel, prepared.outcomes, prepared.document,
                      prepared.space, prepared.provenance, synthetic=False, backend=backend)


def _run_input(
    output: Path, config: DemoConfig, panel: FactorPanel, outcomes: OutcomeStore,
    input_data: dict[str, Any], space: ProposalSpace, provenance: dict[str, object], *, synthetic: bool,
    backend: ProposalBackend | None = None,
) -> dict[str, Any]:
    if config.proposal_mode == "baseline" and backend is not None:
        raise ValueError("baseline mode cannot silently use a proposal backend")
    if config.proposal_mode == "scripted" and backend is None:
        raise ValueError("scripted mode requires an explicit response backend")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("choose an empty output directory to preserve previous experiments")
    output.mkdir(parents=True, exist_ok=True)
    for directory in ("iterations", "reports"):
        (output / directory).mkdir()
    proposer = M1Proposer(space, seed=config.seed)
    features = FeatureView.from_panel(panel, space.factor_ids, dataset_id=str(provenance.get("dataset_id", "synthetic")))
    client = None
    if config.proposal_mode != "baseline":
        client = ProposalClient(backend or OfflineEvidenceBackend(), max_calls=config.proposal_call_budget)
    guided = GuidedProposer(proposer, features, client)

    def call_counts() -> dict[str, Any]:
        return client.counts() if client else {"backend": "none", "proposal_backend_calls": 0,
                                               "llm_calls": 0, "offline_calls": 0,
                                               "token_usage": None, "cost": None}

    expected = {"yes_price", *proposer.space.factor_ids}
    for market, rows in panel.items():
        if not rows or not expected <= {r.factor_id for r in rows}:
            raise ValueError(f"incomplete input features for {market}")
    data_hash = sha256(json.dumps(input_data, sort_keys=True, allow_nan=False).encode()).hexdigest()
    core_names = ("schemas.py", "replay.py", "fast_loop.py", "slow_loop.py",
                  "champion_pool.py", "loop.py", "proposer.py", "report.py")
    core_hashes = {name: sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                   for name in core_names}
    implementation_hashes = {path.name: sha256(path.read_bytes()).hexdigest()
                             for path in sorted(Path(__file__).parent.glob("*.py"))}
    _write(output / "run_config.json", {"config": asdict(config), "space": asdict(proposer.space),
                                        **call_counts(), "scope": provenance["kind"], "synthetic": synthetic})
    _write(output / "input_manifest.json", {**provenance, "sha256": data_hash,
                                            "teammate_core_sha256": core_hashes,
                                            "optimizer_sha256": implementation_hashes,
                                            "search_feature_sha256": features.fingerprint})
    _write(output / "input.json", input_data)
    previous: list[FeedbackPayload] = []
    review: ReviewBundle | None = None
    review_replays = 0
    evaluated = 0
    rounds_done = 0
    level_counts = {"L1": 0, "L2": 0, "L3": 0}
    stop_reason = "round_budget"
    summary: dict[str, Any]
    try:
        with Tracer.open(output / "traces.sqlite3") as tracer, ChampionPool.open(output / "champions.sqlite3") as pool:
            loop = OptimizationLoop(
                fast_evaluator=FastLoopEvaluator(),
                slow_validator=SlowLoopValidator(fee_bps=config.fee_bps, slippage_bps=config.slippage_bps),
                champion_pool=pool, tracer=tracer, top_k=config.top_k,
            )
            for iteration in range(1, config.rounds + 1):
                checks_before = guided.feature_checks
                candidates = guided.propose(previous, config.batch_size, iteration=iteration, review=review)
                _write(output / "proposal_calls.json", client.events if client else [])
                _write(output / "proposal_attempts.json", guided.attempts)
                if not candidates:
                    stop_reason = "space_exhausted"
                    break
                _write(output / "candidate_registry.json", {c.candidate_id: c.to_dict() for c in proposer.candidates})
                round_path = output / "iterations" / f"{iteration:03d}"
                round_path.mkdir()
                _write(round_path / "proposals.json", [c.to_dict() for c in candidates])
                results = loop.run_once(candidates, panel, outcomes)
                previous = [r.feedback for r in results]
                _write(round_path / "results.json", [asdict(r) for r in results])
                _write(round_path / "feedback.json", [f.to_dict() for f in previous])
                reasons = {r.candidate.candidate_id: r.promotion.reason for r in results
                           if r.promotion is not None}
                _write(round_path / "feedback_context.json", {"promotion_reasons": reasons,
                                                               "scope": "development_only"})
                review = build_review(iteration, previous,
                                      candidates={c.candidate_id: c for c in candidates}, features=features)
                review_replays += review.feature_replays
                _write(round_path / "diagnostics.json", review.to_dict())
                _write(round_path / "proposal_attempts.json",
                       [a for a in guided.attempts if a["iteration"] == iteration])
                report = render_report(iteration, previous,
                                       candidates={c.candidate_id: c for c in candidates},
                                       promotion_reasons=reasons)
                report += "\n" + render_review(review)
                report = (f"提议模式：{config.proposal_mode}；后端：{call_counts()['backend']}；"
                          f"累计真实模型调用：{call_counts()['llm_calls']}。\n"
                          "offline-evidence / scripted 为离线接线验证，不是 LLM 实验。\n\n" + report)
                if not synthetic:
                    report = ("数据来源：" + str(provenance["kind"]) + "；历史开发回放，非独立最终测试。\n\n"
                              + "使用公开归档价格与因子，交易执行仍是队友的简化模拟；不代表真实成交或真实策略收益。\n\n"
                              + "可用性假设：" + str(provenance["availability_assumption"]) + "\n\n" + report)
                (output / "reports" / f"iteration-{iteration:03d}.md").write_text(report, encoding="utf-8")
                evaluated += len(results)
                rounds_done += 1
                for candidate in candidates:
                    level_counts[candidate.proposer_level] += 1
                _write(round_path / "counts.json", {
                    "fast_evaluated": len(results), "slow_evaluated": sum(r.slow is not None for r in results),
                    "promoted": sum(bool(r.promotion and r.promotion.promoted) for r in results),
                    "m5_feature_replays": review.feature_replays,
                    "proposal_feature_replays": 2 * (guided.feature_checks - checks_before),
                })
            exported = pool.export_champion_spec()
            champion = pool.current_champion()
            _write(output / "champion.json", {
                "candidate": json.loads(exported) if exported else None,
                "version": champion.version if champion else None,
                "registered": False, "activated": False,
            })
            _write(output / "trace_tree.json", [node.to_dict() for node in tracer.roots()])
            summary = {"status": "completed", "stop_reason": stop_reason, "rounds": rounds_done,
                       "candidate_evaluations": evaluated, "proposer_levels": level_counts,
                       "space_size": proposer.space.size, **call_counts(),
                       "proposal_mode": config.proposal_mode,
                       "m5_feature_replays": review_replays,
                       "proposal_feature_replays": 2 * guided.feature_checks,
                       "accepted_guided_edits": sum(a["status"] == "accepted" for a in guided.attempts),
                       "rejected_guided_edits": sum(a["status"] == "rejected" for a in guided.attempts),
                       "m5_method": "deterministic_evidence_rules",
                       "m3_data_protocol": "legacy_same_development_panel_slices",
                       "independent_validation_used": False,
                       "champion_versions": len(pool.ledger()), "registered": False,
                       "final_holdout_used": False, "data_sha256": data_hash,
                       "synthetic": synthetic, "data_source": provenance["kind"],
                       "market_count": len(panel),
                       "scope": "synthetic functional demo; development slices, not final OOS or real PnL"
                       if synthetic else "public historical development replay; simplified execution, not independent OOS",
                       "provenance": provenance}
    except Exception as exc:
        _write(output / "summary.json", {"status": "failed", "error_type": type(exc).__name__,
                                          "rounds_completed": rounds_done, **call_counts()})
        raise
    _write(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--demo", action="store_true",
                        help="explicitly acknowledge synthetic development-only data")
    inputs.add_argument("--input", type=Path, help="prepared development panel JSON with point-in-time provenance")
    parser.add_argument("--output", type=Path, default=Path(".data/optimizer-demo"))
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--proposal-mode", choices=("baseline", "offline-evidence", "scripted"), default="baseline")
    parser.add_argument("--proposal-call-budget", type=int, default=20)
    parser.add_argument("--proposal-responses", type=Path, help="JSON array of raw response strings; scripted mode only")
    args = parser.parse_args()
    try:
        config = DemoConfig(rounds=args.rounds, batch_size=args.batch_size, top_k=args.top_k, seed=args.seed,
                            proposal_mode=args.proposal_mode, proposal_call_budget=args.proposal_call_budget)
        backend = None
        if args.proposal_mode == "scripted":
            if args.proposal_responses is None:
                raise ValueError("scripted mode requires --proposal-responses")
            responses = json.loads(args.proposal_responses.read_text(encoding="utf-8"))
            if not isinstance(responses, list) or not all(isinstance(value, str) for value in responses):
                raise ValueError("response fixture must be an array of raw JSON strings")
            backend = ScriptedBackend(tuple(responses))
        elif args.proposal_responses is not None:
            raise ValueError("--proposal-responses is only used in scripted mode")
        summary = (run_demo(args.output, config, backend=backend) if args.demo else
                   run_historical(args.input, args.output, config, backend=backend))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
