"""Fetch one pinned public historical episode and prepare development input.

Run via uv run --with pyarrow==26.0.0 python scripts/fetch_optimizer_history.py
--output .data/public-history-001. No trading API, keys or database required.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.request import urlopen

REF = "611d66941717310858683278940df21c33c406f2"
EPISODE = "KXNFLGAME-26JAN11BUFJAC"
BASE = f"https://raw.githubusercontent.com/Oddpool/PredictionMarketBench/{REF}/episodes/{EPISODE}"
FILES = {
    "metadata.json": "907d798be0fda4268857724bc58d27be920c60c989c776d78dffdc8cc524f34e",
    "settlement.json": "b102105271cf90d0f3cc4eca8282033fa95646389ee6497371c0cbdfd8a6caac",
    "orderbook.parquet": "1b19d38ab37d91c93ddf74a17ebe96cd99cf33ab3a5cf2bc73c55de827bd9c3c",
}


def prepare(raw_root: Path, output: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    for name, digest in FILES.items():
        if sha256((raw_root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"pinned source checksum mismatch: {name}")
    settlements = json.loads((raw_root / "settlement.json").read_text())
    rows = sorted(pq.read_table(raw_root / "orderbook.parquet").to_pylist(),
                  key=lambda r: (r["ts"], r["sequence_id"]))
    observations, outcomes, settled_times = [], [], []
    sampled: set[tuple[str, int]] = set()
    rejected: Counter[str] = Counter()
    for row in rows:
        ticker, timestamp = row["ticker"], row["ts"]
        settlement = settlements.get(ticker)
        if settlement is None or settlement.get("result") not in {"YES", "NO"}:
            rejected["unknown_settlement"] += 1
            continue
        settled = datetime.fromisoformat(settlement["settled_ts"])
        if timestamp >= settled:
            rejected["at_or_after_settlement"] += 1
            continue
        key = (ticker, int(timestamp.timestamp() // 3600))
        if key in sampled:
            rejected["hourly_subsampling"] += 1
            continue
        yes, no = json.loads(row["yes_bids"]), json.loads(row["no_bids"])
        if not yes or not no:
            rejected["one_sided_book"] += 1
            continue
        if any(not isfinite(float(v["size"])) or v["size"] < 0
               or not 0 < v["price_cents"] < 100 for v in yes + no):
            rejected["invalid_book"] += 1
            continue
        bid = max(v["price_cents"] for v in yes) / 100
        ask = 1 - max(v["price_cents"] for v in no) / 100
        yes_depth, no_depth = sum(v["size"] for v in yes), sum(v["size"] for v in no)
        if bid > ask + 1e-9 or yes_depth + no_depth <= 0:
            rejected["crossed_or_zero_depth"] += 1
            continue
        probability = (bid + ask) / 2
        bias = probability - 0.1 if probability < 0.1 else probability - 0.9 if probability > 0.9 else 0.0
        identity = f"{EPISODE}:{ticker}:{timestamp.isoformat()}:{row['sequence_id']}"
        observations.append({
            "sample_id": identity, "market_id": ticker, "event_id": EPISODE,
            "observed_at": timestamp.isoformat(), "available_at": timestamp.isoformat(),
            "decision_at": timestamp.isoformat(), "market_probability": probability,
            "factors": [["orderbook_imbalance", (yes_depth - no_depth) / (yes_depth + no_depth)],
                        ["favorite_longshot_bias", bias]],
        })
        outcomes.append({"sample_id": identity, "value": int(settlement["result"] == "YES"),
                         "available_at": settled.isoformat()})
        settled_times.append(settled)
        sampled.add(key)
    if not observations:
        raise ValueError("no usable pre-settlement historical rows")
    document = {
        "dataset_id": f"PredictionMarketBench-{REF[:12]}-development", "role": "development",
        "source_kind": "public_kalshi_archive", "observations": observations, "outcomes": outcomes,
        "as_of": max(settled_times).isoformat(),
        "availability_assumption": "archive event timestamp used as replay availability proxy; historical reception time unverified",
    }
    manifest = {"source_repository": "Oddpool/PredictionMarketBench", "source_ref": REF,
                "episode": EPISODE, "raw_rows": len(rows), "prepared_rows": len(observations),
                "independent_event_groups": 1, "market_count": len(settlements),
                "files": {name: {"url": f"{BASE}/{name}", "sha256": digest} for name, digest in FILES.items()},
                "rejected": dict(rejected), "input": "development.json",
                "price_reference": "midpoint of inferred YES bid/ask, not a guaranteed executable price",
                "pms_kalshi_adapter_used": False,
                "scope": "public archive development smoke; no final test or real execution claim"}
    (output / "development.json").write_text(json.dumps(document, ensure_ascii=False, indent=2))
    (output / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, help="use previously downloaded pinned files, without network")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("choose an empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    raw_root = args.raw_dir or args.output / "raw"
    if args.raw_dir is None:
        raw_root.mkdir()
        for name, digest in FILES.items():
            with urlopen(f"{BASE}/{name}", timeout=60) as response:
                data = response.read(12_000_001)
            if len(data) > 12_000_000 or sha256(data).hexdigest() != digest:
                raise ValueError(f"pinned source download failed integrity check: {name}")
            (raw_root / name).write_bytes(data)
    print(json.dumps(prepare(raw_root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
