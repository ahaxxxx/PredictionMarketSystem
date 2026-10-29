"""Read prepared historical panels without inventing unavailable factors.

    Input is an offline snapshot, not a venue API or trading adapter.
    Observation/label availability checks precede the legacy replay modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any

from pms.factors.base import FactorValueRow
from pms.optimizer.m1 import ProposalSpace
from pms.research.cache import FactorPanel


@dataclass(frozen=True, slots=True)
class HistoricalOutcomes:
    values: tuple[tuple[str, float], ...]

    def outcome(self, market_id: str) -> float | None:
        return next((value for key, value in self.values if key == market_id), None)


@dataclass(frozen=True, slots=True)
class HistoricalInput:
    panel: FactorPanel
    outcomes: HistoricalOutcomes
    space: ProposalSpace
    document: dict[str, Any]
    provenance: dict[str, object]


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _time(value: object) -> datetime:
    result = datetime.fromisoformat(str(value))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamps must include timezone")
    return result


def load_historical_input(path: Path) -> HistoricalInput:
    content = path.read_bytes()
    raw = _object(json.loads(content))
    if raw.get("role") != "development":
        raise ValueError("search input must explicitly be development; holdouts cannot feed M1")
    for key in ("dataset_id", "source_kind", "availability_assumption"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"missing provenance field: {key}")
    as_of = _time(raw["as_of"])
    labels: dict[str, tuple[int, datetime]] = {}
    for value in raw["outcomes"]:
        row = _object(value)
        sample = str(row["sample_id"])
        if sample in labels or type(row["value"]) is not int or row["value"] not in (0, 1):
            raise ValueError("unique binary integer outcomes required")
        available = _time(row["available_at"])
        if available > as_of:
            raise ValueError("outcome is unavailable at research cutoff")
        labels[sample] = (row["value"], available)
    panel: dict[str, list[FactorValueRow]] = {}
    outcomes: dict[str, float] = {}
    seen: set[str] = set()
    seen_points: set[tuple[str, datetime]] = set()
    events: set[str] = set()
    factor_ids: tuple[str, ...] | None = None
    for value in raw["observations"]:
        row = _object(value)
        sample, market = str(row["sample_id"]), str(row["market_id"])
        if not sample or not market or not row.get("event_id") or sample in seen or sample not in labels:
            raise ValueError("unique observations with matching labels and event IDs required")
        observed, available, decision = (_time(row[key]) for key in ("observed_at", "available_at", "decision_at"))
        if not observed <= available <= decision < labels[sample][1] <= as_of:
            raise ValueError("observation, decision and settlement times are inconsistent")
        if (market, decision) in seen_points:
            raise ValueError("duplicate market/decision timestamp cannot be silently overwritten")
        seen.add(sample)
        seen_points.add((market, decision))
        events.add(str(row["event_id"]))
        probability = float(row["market_probability"])
        if not isfinite(probability) or not 0 < probability < 1:
            raise ValueError("market probability must be strictly inside (0,1)")
        factors = tuple((str(name), float(v)) for name, v in row["factors"])
        names = tuple(name for name, _ in factors)
        if len(set(names)) != len(names) or any(not isfinite(v) for _, v in factors):
            raise ValueError("unique finite factors required")
        if factor_ids is None:
            factor_ids = names
        elif set(names) != set(factor_ids):
            raise ValueError("required factor panel is incomplete; no imputation allowed")
        actual = float(labels[sample][0])
        if market in outcomes and outcomes[market] != actual:
            raise ValueError("conflicting resolutions for one market")
        outcomes[market] = actual
        values = (("yes_price", probability), *factors)
        panel.setdefault(market, []).extend(FactorValueRow(name, "", market, decision, v) for name, v in values)
    if not seen or seen != set(labels) or factor_ids is None:
        raise ValueError("nonempty one-to-one observation/label mapping required")
    space = ProposalSpace(factor_ids=factor_ids)
    return HistoricalInput(
        {market: tuple(sorted(rows, key=lambda r: r.ts)) for market, rows in panel.items()},
        HistoricalOutcomes(tuple(sorted(outcomes.items()))), space, raw,
        {"kind": raw["source_kind"], "dataset_id": raw["dataset_id"],
         "source_file_sha256": sha256(content).hexdigest(), "as_of": as_of.isoformat(),
         "availability_assumption": raw["availability_assumption"],
         "independent_event_groups": len(events), "market_count": len(panel),
         "observation_count": len(seen), "pms_venue_adapter_used": False,
         "scope": "offline historical development replay; simplified execution only"},
    )
