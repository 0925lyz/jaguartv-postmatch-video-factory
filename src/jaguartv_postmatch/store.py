from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .task1 import Task1Fixture
from .util import stable_json_hash, utc_now


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_date TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    last_successful_artifact TEXT NOT NULL DEFAULT '',
    error_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS fixtures (
    task1_fixture_id TEXT PRIMARY KEY,
    source_fixture_id TEXT,
    composite_key TEXT NOT NULL UNIQUE,
    match_date TEXT NOT NULL,
    kickoff_brt TEXT NOT NULL,
    competition TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    channels_json TEXT NOT NULL,
    source_manifest TEXT NOT NULL,
    pre_match_poster TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS result_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task1_fixture_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    revision_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task1_fixture_id, revision_hash),
    UNIQUE(task1_fixture_id, revision_number),
    FOREIGN KEY(task1_fixture_id) REFERENCES fixtures(task1_fixture_id)
);
CREATE INDEX IF NOT EXISTS ix_result_current
    ON result_revisions(task1_fixture_id, is_current);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_key TEXT PRIMARY KEY,
    task1_fixture_id TEXT,
    result_revision_id INTEGER,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task1_fixture_id) REFERENCES fixtures(task1_fixture_id),
    FOREIGN KEY(result_revision_id) REFERENCES result_revisions(id)
);
CREATE TABLE IF NOT EXISTS uploads (
    idempotency_key TEXT PRIMARY KEY,
    artifact_key TEXT NOT NULL,
    server_item_id TEXT UNIQUE,
    status TEXT NOT NULL,
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(artifact_key) REFERENCES artifacts(artifact_key)
);
"""


class WorkflowStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def begin_run(self, run_date: str) -> None:
        timestamp = utc_now()
        self.connection.execute(
            """INSERT INTO workflow_runs(run_date,started_at,updated_at,status)
               VALUES(?,?,?,'RUNNING')
               ON CONFLICT(run_date) DO UPDATE SET updated_at=excluded.updated_at,status='RUNNING',error_json='{}'""",
            (run_date, timestamp, timestamp),
        )
        self.connection.commit()

    def finish_run(
        self,
        run_date: str,
        status: str,
        *,
        last_successful_artifact: str = "",
        error: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """UPDATE workflow_runs
               SET updated_at=?,status=?,last_successful_artifact=?,error_json=?
               WHERE run_date=?""",
            (
                utc_now(),
                status,
                last_successful_artifact,
                json.dumps(error or {}, ensure_ascii=False, sort_keys=True),
                run_date,
            ),
        )
        self.connection.commit()

    def upsert_fixture(self, fixture: Task1Fixture) -> None:
        timestamp = utc_now()
        values = fixture.to_dict()
        self.connection.execute(
            """INSERT INTO fixtures(
                 task1_fixture_id,source_fixture_id,composite_key,match_date,kickoff_brt,
                 competition,home_team,away_team,channels_json,source_manifest,pre_match_poster,
                 created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(task1_fixture_id) DO UPDATE SET
                 source_fixture_id=excluded.source_fixture_id,
                 composite_key=excluded.composite_key,
                 match_date=excluded.match_date,
                 kickoff_brt=excluded.kickoff_brt,
                 competition=excluded.competition,
                 home_team=excluded.home_team,
                 away_team=excluded.away_team,
                 channels_json=excluded.channels_json,
                 source_manifest=excluded.source_manifest,
                 pre_match_poster=excluded.pre_match_poster,
                 updated_at=excluded.updated_at""",
            (
                values["task1_fixture_id"],
                values["source_fixture_id"],
                values["composite_key"],
                values["match_date"],
                values["kickoff_brt"],
                values["competition"],
                values["home_team"],
                values["away_team"],
                json.dumps(values["channels"], ensure_ascii=False),
                values["source_manifest"],
                values["pre_match_poster"],
                timestamp,
                timestamp,
            ),
        )
        self.connection.commit()

    def record_result(self, task1_fixture_id: str, result: dict[str, Any]) -> tuple[int, bool]:
        revision_payload = {
            key: result.get(key)
            for key in (
                "result_status",
                "home_score",
                "away_score",
                "extra_time",
                "penalty_shootout",
                "official_match_date",
            )
        }
        revision_hash = stable_json_hash(revision_payload)
        prior = self.connection.execute(
            "SELECT id FROM result_revisions WHERE task1_fixture_id=? AND revision_hash=?",
            (task1_fixture_id, revision_hash),
        ).fetchone()
        if prior:
            return int(prior["id"]), False

        current = self.connection.execute(
            """SELECT id,revision_number FROM result_revisions
               WHERE task1_fixture_id=? AND is_current=1""",
            (task1_fixture_id,),
        ).fetchone()
        revision_number = int(current["revision_number"]) + 1 if current else 1
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if current:
                self.connection.execute(
                    "UPDATE result_revisions SET is_current=0 WHERE id=?", (current["id"],)
                )
                self.connection.execute(
                    """UPDATE artifacts SET status='STALE',updated_at=?
                       WHERE task1_fixture_id=? AND result_revision_id=?
                         AND artifact_key NOT IN (SELECT artifact_key FROM uploads WHERE status='UPLOADED')""",
                    (utc_now(), task1_fixture_id, current["id"]),
                )
            cursor = self.connection.execute(
                """INSERT INTO result_revisions(
                     task1_fixture_id,revision_number,revision_hash,is_current,result_json,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    task1_fixture_id,
                    revision_number,
                    revision_hash,
                    1,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return int(cursor.lastrowid), True

    def current_results(self, match_date: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT r.id,r.revision_number,r.result_json
               FROM result_revisions r JOIN fixtures f ON f.task1_fixture_id=r.task1_fixture_id
               WHERE f.match_date=? AND r.is_current=1 ORDER BY f.kickoff_brt,f.task1_fixture_id""",
            (match_date,),
        ).fetchall()
        return [
            {
                "revision_id": int(row["id"]),
                "revision_number": int(row["revision_number"]),
                **json.loads(row["result_json"]),
            }
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        return {
            table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("fixtures", "result_revisions", "artifacts", "uploads")
        }
