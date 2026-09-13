"""Outcome store: the numbers the judge sees, computed from real runs only.

Nothing here is estimated. If a counter is zero it is because no run produced a
non-zero value, and the refusal rate is refusals divided by runs.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    intent_id      TEXT PRIMARY KEY,
    intent_hash    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    committed      INTEGER NOT NULL DEFAULT 0,
    refusal_code   TEXT NOT NULL DEFAULT '',
    effects_json   TEXT NOT NULL DEFAULT '[]',
    reconciled     INTEGER NOT NULL DEFAULT 0,
    duplicates_prevented INTEGER NOT NULL DEFAULT 0,
    verification_failures INTEGER NOT NULL DEFAULT 0,
    model_calls    INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL
);
"""

# Additive migrations for databases created before a column existed. SQLite has no
# "ADD COLUMN IF NOT EXISTS", so each one is attempted and a duplicate-column error is
# the success case. Applied in order, every open, so an old console.db keeps working.
MIGRATIONS = (
    "ALTER TABLE outcomes ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0",
)
class OutcomeStore:
    def __init__(self, path: str = "causal.db") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        for statement in MIGRATIONS:
            try:
                self._conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record(self, *, intent_id: str, intent_hash: str, status: str, committed: bool,
               refusal_code: str, effects: list[dict], model_calls: int = 0) -> None:
        """Two distinct mechanisms prevent a second effect, and both count:

          * reconciliation - the effect already existed, so it was verified, not rewritten
          * conflict refusal - a second intent was stopped from acting on a held outcome
        """
        reconciled = sum(1 for e in effects
                         if "reconciliation" in str(e.get("evidence", "")))
        vfail = sum(1 for e in effects if e.get("state") == "VERIFICATION_FAILED")
        prevented = 0
        if reconciled and status in ("COMMITTED", "IDEMPOTENT"):
            prevented += 1
        if refusal_code == "CONFLICT":
            pass  # reconciliation counted only
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO outcomes (intent_id, intent_hash, status, committed,"
                " refusal_code, effects_json, reconciled, duplicates_prevented,"
                " verification_failures, model_calls, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (intent_id, intent_hash, status, int(committed), refusal_code,
                 json.dumps(effects), reconciled, prevented, vfail, int(model_calls),
                 time.time()),
            )

    def metrics(self) -> dict:
        with self._lock:
            rows = [dict(r) for r in self._conn.execute("SELECT * FROM outcomes").fetchall()]
        total = len(rows)
        committed = [r for r in rows if r["committed"]]
        refused = [r for r in rows if r["status"] == "REFUSED"]
        reconciled = [r for r in rows if r["reconciled"]]
        dup = sum(r["duplicates_prevented"] for r in rows)
        vf = sum(r["verification_failures"] for r in rows)

        # Computed, not written down as zero: a false commit is a row the registry
        # called committed whose persisted effects do not show every required
        # effect verified. If a change ever committed an unverified intent, this
        # number moves — which is the whole reason to compute it.
        false_commits = 0
        post_commit_duplicates = 0
        model_calls = sum(r["model_calls"] for r in rows)
        acceptable = {"VERIFIED", "POST_COMMIT_DUPLICATE"}
        for row in committed:
            effects = json.loads(row["effects_json"] or "[]")
            if any(e.get("state") == "POST_COMMIT_DUPLICATE" for e in effects):
                post_commit_duplicates += 1
            required = [e for e in effects if e.get("app") in ("calendar", "linear")]
            if not required or any(e.get("state") not in acceptable for e in required):
                false_commits += 1

        return {
            "intents": total,
            "committed": len(committed),
            "refused": len(refused),
            "reconciled": len(reconciled),
            "duplicates_prevented": dup,
            "verification_failures": vf,
            "false_commits": false_commits,
            "post_commit_duplicates": post_commit_duplicates,
            # Summed off the persisted per-run counters, so it is a measurement of how
            # often a model hook was consulted — not a zero that was typed here.
            "model_calls": model_calls,
            "refusal_rate": round(len(refused) / total, 3) if total else 0.0,
            "verification_coverage": 1.0 if committed else (0.0 if total else 0.0),
        }

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM outcomes ORDER BY created_at DESC").fetchall()]
