"""M4 — champion pool and experiment ledger.

SQLite-backed archive for every candidate the loop has ever seen: the spec,
its fast/slow scores, and the promotion history.  A candidate only becomes
champion if it clears the gates AND beats the reigning champion on the
out-of-sample composite, so the pool never regresses.  All data stays local
— this is the self-hosted experiment record that replaces any SaaS dashboard.

Champion specs can be exported as JSON for the Controller layer to register
as the fourth strategy (M1/博湛's wiring seam).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from pms.optimizer.schemas import (
    CandidateSpec,
    FastScore,
    PromotionDecision,
    SlowScore,
)

__all__ = ("ChampionPool", "ChampionRecord")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    spec_json    TEXT NOT NULL,
    proposer_level TEXT NOT NULL,
    parent_candidate_id TEXT,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    stage        TEXT NOT NULL,
    score_json   TEXT NOT NULL,
    recorded_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS champions (
    version      INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    fast_score_json  TEXT NOT NULL,
    slow_score_json TEXT NOT NULL,
    promoted_at  REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'
);
"""


@dataclass(frozen=True, slots=True)
class ChampionRecord:
    version: int
    candidate_id: str
    fast_score: dict[str, Any]
    slow_score: dict[str, Any]
    status: str


class ChampionPool:
    """Versioned archive of evaluated candidates and promotion decisions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        max_fast_brier: float = 0.25,
        require_positive_oos_pnl: bool = True,
    ) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()
        self._max_fast_brier = max_fast_brier
        self._require_positive_oos_pnl = require_positive_oos_pnl

    @classmethod
    def open(cls, path: str | Path, **gates: Any) -> Self:
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        return cls(connection, **gates)

    @classmethod
    def in_memory(cls, **gates: Any) -> Self:
        return cls(sqlite3.connect(":memory:"), **gates)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- recording --------------------------------------------------------

    def record_candidate(self, candidate: CandidateSpec) -> None:
        import time as _time

        self._connection.execute(
            """
            INSERT OR REPLACE INTO candidates (
                candidate_id, spec_json, proposer_level,
                parent_candidate_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id,
                candidate.to_json(),
                candidate.proposer_level,
                candidate.parent_candidate_id,
                _time.time(),
            ),
        )
        self._connection.commit()

    def record_score(
        self,
        candidate_id: str,
        stage: str,
        score: FastScore | SlowScore,
    ) -> None:
        import time as _time

        self._connection.execute(
            """
            INSERT INTO scores (candidate_id, stage, score_json, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (candidate_id, stage, json.dumps(score.to_dict(), sort_keys=True), _time.time()),
        )
        self._connection.commit()

    # ---- promotion ----------------------------------------------------------

    def promote(
        self,
        candidate: CandidateSpec,
        fast: FastScore,
        slow: SlowScore,
    ) -> PromotionDecision:
        self.record_candidate(candidate)
        self.record_score(candidate.candidate_id, "fast", fast)
        self.record_score(candidate.candidate_id, "slow", slow)

        if fast.brier > self._max_fast_brier:
            return PromotionDecision(False, None, "fast brier above gate")
        oos_pnl = slow.oos_pnl()
        if self._require_positive_oos_pnl and oos_pnl is not None and oos_pnl <= 0.0:
            return PromotionDecision(False, None, "non-positive out-of-sample pnl")

        incumbent = self.current_champion()
        challenger_value = _champion_value(fast, slow)
        if incumbent is not None:
            incumbent_score = self._champion_scores(incumbent.version)
            incumbent_value = (
                _champion_value(incumbent_score[0], incumbent_score[1])
                if incumbent_score is not None
                else float("-inf")
            )
            if challenger_value <= incumbent_value:
                return PromotionDecision(
                    False,
                    None,
                    f"composite {challenger_value:.4f} <= incumbent {incumbent_value:.4f}",
                )

        version = (incumbent.version + 1) if incumbent is not None else 1
        import time as _time

        if incumbent is not None:
            self._connection.execute(
                "UPDATE champions SET status = 'superseded' WHERE version = ?",
                (incumbent.version,),
            )
        self._connection.execute(
            """
            INSERT INTO champions (
                version, candidate_id, fast_score_json, slow_score_json,
                promoted_at, status
            ) VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (
                version,
                candidate.candidate_id,
                json.dumps(fast.to_dict(), sort_keys=True),
                json.dumps(slow.to_dict(), sort_keys=True),
                _time.time(),
            ),
        )
        self._connection.commit()
        return PromotionDecision(True, version, "promoted")

    def current_champion(self) -> ChampionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM champions WHERE status = 'active' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return ChampionRecord(
            version=int(row["version"]),
            candidate_id=row["candidate_id"],
            fast_score=json.loads(row["fast_score_json"]),
            slow_score=json.loads(row["slow_score_json"]),
            status=row["status"],
        )

    def ledger(self) -> list[ChampionRecord]:
        rows = self._connection.execute(
            "SELECT * FROM champions ORDER BY version ASC"
        ).fetchall()
        return [
            ChampionRecord(
                version=int(row["version"]),
                candidate_id=row["candidate_id"],
                fast_score=json.loads(row["fast_score_json"]),
                slow_score=json.loads(row["slow_score_json"]),
                status=row["status"],
            )
            for row in rows
        ]

    def export_champion_spec(self) -> str | None:
        """JSON spec of the active champion, for registering as the 4th strategy."""
        champion = self.current_champion()
        if champion is None:
            return None
        row = self._connection.execute(
            "SELECT spec_json FROM candidates WHERE candidate_id = ?",
            (champion.candidate_id,),
        ).fetchone()
        return str(row["spec_json"]) if row is not None else None

    def _champion_scores(
        self, version: int
    ) -> tuple[FastScore, SlowScore] | None:
        row = self._connection.execute(
            "SELECT fast_score_json, slow_score_json FROM champions WHERE version = ?",
            (version,),
        ).fetchone()
        if row is None:
            return None
        return (
            FastScore.from_dict(json.loads(row["fast_score_json"])),
            SlowScore.from_dict(json.loads(row["slow_score_json"])),
        )


def _champion_value(fast: FastScore, slow: SlowScore) -> float:
    """Higher-is-better composite used to compare champions."""
    oos_brier = slow.oos_brier()
    calibration = 1.0 - (oos_brier if oos_brier is not None else 1.0)
    return slow.oos_pnl() + calibration
