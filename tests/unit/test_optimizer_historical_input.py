from __future__ import annotations

from pathlib import Path
import json
from typing import Any

import pytest

from pms.optimizer.data import load_historical_input
from pms.optimizer.run import DemoConfig, run_historical


def document() -> dict[str, Any]:
    return {"dataset_id": "historical-input-unit-fixture", "role": "development",
            "source_kind": "unit_test_fixture", "availability_assumption": "explicit fixture times",
            "as_of": "2025-01-04T00:00:00+00:00",
            "observations": [
                {"sample_id": str(i), "market_id": f"m{i}", "event_id": f"e{i}",
                 "observed_at": f"2025-01-0{i+1}T00:00:00+00:00",
                 "available_at": f"2025-01-0{i+1}T00:00:00+00:00",
                 "decision_at": f"2025-01-0{i+1}T00:00:00+00:00", "market_probability": 0.5,
                 "factors": [["orderbook_imbalance", -0.5 if i == 0 else 0.5],
                             ["favorite_longshot_bias", 0.0]]} for i in range(2)],
            "outcomes": [{"sample_id": str(i), "value": i,
                          "available_at": "2025-01-03T00:00:00+00:00"} for i in range(2)]}


def save(tmp_path: Path, raw: dict[str, Any]) -> Path:
    p = tmp_path / "input.json"
    p.write_text(json.dumps(raw))
    return p


def test_archive_uses_available_factors_instead_of_fabricating_prior(tmp_path: Path) -> None:
    data = load_historical_input(save(tmp_path, document()))
    assert data.space.factor_ids == ("orderbook_imbalance", "favorite_longshot_bias")
    assert data.provenance["independent_event_groups"] == 2
    assert data.outcomes.outcome("m1") == 1.0
    assert all(r.factor_id != "metaculus_prior" for rows in data.panel.values() for r in rows)


@pytest.mark.parametrize("fault", ["test_role", "future_label", "late_observation", "missing_factor",
                                  "duplicate_point", "conflicting_resolution", "binary_bool"])
def test_invalid_historical_evidence_rejected(tmp_path: Path, fault: str) -> None:
    raw = document()
    if fault == "test_role":
        raw["role"] = "test"
    elif fault == "future_label":
        raw["outcomes"][0]["available_at"] = "2025-01-05T00:00:00+00:00"
    elif fault == "late_observation":
        raw["observations"][0]["decision_at"] = "2025-01-03T00:00:00+00:00"
    elif fault == "missing_factor":
        raw["observations"][1]["factors"] = [["orderbook_imbalance", 0.5]]
    elif fault == "duplicate_point":
        raw["observations"][1] = {**raw["observations"][0], "sample_id": "1"}
    elif fault == "conflicting_resolution":
        raw["observations"][1]["market_id"] = "m0"
    elif fault == "binary_bool":
        raw["outcomes"][0]["value"] = False
    with pytest.raises(ValueError):
        load_historical_input(save(tmp_path, raw))


def test_file_input_runs_real_loop_and_records_provenance(tmp_path: Path) -> None:
    path = save(tmp_path, document())
    output = tmp_path / "run"
    result = run_historical(path, output, DemoConfig(rounds=1, batch_size=2, top_k=1))
    assert result["synthetic"] is False
    assert result["data_source"] == "unit_test_fixture"
    assert result["candidate_evaluations"] == 2
    manifest = json.loads((output / "input_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_file_sha256"]
    assert manifest["independent_event_groups"] == 2
    assert "公开归档价格" in (output / "reports/iteration-001.md").read_text(encoding="utf-8")
