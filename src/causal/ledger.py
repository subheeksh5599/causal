"""Effect state machine with enforced legal transitions.

EFFECTED is what the write path claims.
VERIFIED is what an independent read proves.
COMMITTED is a property of the intent, not of an effect.

An effect cannot jump PLANNED -> VERIFIED, and a VERIFIED effect cannot quietly
go back to REQUESTED. Both are tested.
"""

from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS effects (
    intent_hash     TEXT NOT NULL,
    intent_id       TEXT NOT NULL,
    effect_id       TEXT NOT NULL,
    app             TEXT NOT NULL,
    operation       TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    external_id     TEXT NOT NULL DEFAULT '',
    attempts        INTEGER NOT NULL DEFAULT 0,
    evidence        TEXT NOT NULL DEFAULT '{}',
    updated_at      REAL NOT NULL,
    PRIMARY KEY (intent_hash, effect_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_effect_per_key ON effects(idempotency_key);

CREATE TABLE IF NOT EXISTS reconciliations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_hash    TEXT NOT NULL,
    effect_id      TEXT NOT NULL,
    ambiguous_state TEXT NOT NULL,
    confidence     TEXT NOT NULL,
    found_existing INTEGER NOT NULL DEFAULT 0,
    strategy       TEXT NOT NULL DEFAULT '',
    detail         TEXT NOT NULL DEFAULT '{}',
    created_at     REAL NOT NULL
);

-- A human decision, recorded with who made it. Kept separate from the effect row
-- because the approval is evidence about a person, not about the effect.
CREATE TABLE IF NOT EXISTS approvals (
    intent_hash TEXT NOT NULL,
    effect_id   TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    PRIMARY KEY (intent_hash, effect_id)
);
"""

PLANNED = "PLANNED"
REQUESTED = "REQUESTED"
EFFECTED = "EFFECTED"
VERIFYING = "VERIFYING"
VERIFIED = "VERIFIED"
UNKNOWN = "UNKNOWN"
RECONCILING = "RECONCILING"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS"
ESCALATED = "ESCALATED"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
REJECTED = "REJECTED"
# Absence could not be established on a surface whose read cannot enumerate the
# domain. Distinct from NOT_FOUND, which is a positive finding and the only
# retryable state.
NOT_PROVABLE = "NOT_PROVABLE"
# A second, equivalent external object appeared after this effect was verified and
# the intent was committed. The world now holds a duplicate CAUSAL did not create
# and cannot delete, so the effect is flagged and escalated rather than rewritten.
POST_COMMIT_DUPLICATE = "POST_COMMIT_DUPLICATE"
# An effect that reaches the outside world waits here for a human. Nothing else does.
AWAITING_APPROVAL = "AWAITING_APPROVAL"

TERMINAL = {VERIFIED, REJECTED, ESCALATED, POST_COMMIT_DUPLICATE}

LEGAL_TRANSITIONS: dict[str, set[str]] = {
    # PLANNED -> RECONCILING is the takeover path: a worker inheriting an intent the
    # previous worker may have acted on has an unknown outcome to settle before it
    # writes, which is exactly what reconciliation is for.
    PLANNED: {REQUESTED, REJECTED, RECONCILING, AWAITING_APPROVAL},
    REQUESTED: {EFFECTED, UNKNOWN, REJECTED},
    EFFECTED: {VERIFYING, REJECTED},
    VERIFYING: {VERIFIED, VERIFICATION_FAILED, UNKNOWN},
    UNKNOWN: {RECONCILING, REJECTED},
    RECONCILING: {VERIFIED, NOT_FOUND, NOT_PROVABLE, AMBIGUOUS, UNKNOWN, REJECTED},
    NOT_FOUND: {REQUESTED, REJECTED},
    # no path back to REQUESTED: not-provable absence must never retry
    NOT_PROVABLE: {ESCALATED, REJECTED},
    AWAITING_APPROVAL: {REQUESTED, REJECTED},
    AMBIGUOUS: {ESCALATED, REJECTED},
    VERIFICATION_FAILED: {VERIFYING, REJECTED},
    VERIFIED: {AMBIGUOUS, POST_COMMIT_DUPLICATE},
    POST_COMMIT_DUPLICATE: {ESCALATED},
    ESCALATED: set(),
    REJECTED: set(),
}

# states from which a duplicate write is forbidden until reconciliation completes
NO_RETRY_STATES = {UNKNOWN, RECONCILING, AMBIGUOUS, VERIFYING, EFFECTED, VERIFIED,
                   NOT_PROVABLE, POST_COMMIT_DUPLICATE, AWAITING_APPROVAL}


class IllegalTransition(RuntimeError):
    pass


class EffectLedger:
    def __init__(self, path: str = "causal.db") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- planning ----------------------------------------------------
    def plan(self, *, intent_hash: str, intent_id: str, effect_id: str, app: str,
             operation: str, idempotency_key: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO effects (intent_hash, intent_id, effect_id, app, operation,"
                " state, idempotency_key, external_id, attempts, evidence, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (intent_hash, intent_id, effect_id, app, operation, PLANNED,
                 idempotency_key, "", 0, "{}", time.time()),
            )

    # ---- transitions --------------------------------------------------
    def transition(self, intent_hash: str, effect_id: str, new_state: str, *,
                   external_id: str | None = None, evidence: dict | None = None,
                   count_attempt: bool = False) -> dict:
        with self._lock:
            row = self.get(intent_hash, effect_id)
            if row is None:
                raise IllegalTransition(f"unknown effect {intent_hash}/{effect_id}")
            current = row["state"]
            if new_state != current and new_state not in LEGAL_TRANSITIONS.get(current, set()):
                raise IllegalTransition(f"{current} -> {new_state} is not a legal transition")
            import json

            sets = ["state=?", "updated_at=?"]
            params: list = [new_state, time.time()]
            if external_id is not None:
                sets.append("external_id=?")
                params.append(external_id)
            if evidence is not None:
                sets.append("evidence=?")
                params.append(json.dumps(evidence))
            if count_attempt:
                sets.append("attempts=attempts+1")
            params += [intent_hash, effect_id]
            self._conn.execute(f"UPDATE effects SET {', '.join(sets)} WHERE intent_hash=? AND effect_id=?", params)
            return self.get(intent_hash, effect_id) or {}

    def invalidate_verified(self, intent_hash: str, effect_id: str, reason: str) -> dict:
        """The only sanctioned path out of VERIFIED. Explicit, audited, never implicit."""
        with self._lock:
            row = self.get(intent_hash, effect_id)
            if row is None or row["state"] != VERIFIED:
                raise IllegalTransition("invalidate_verified requires a VERIFIED effect")
            self._conn.execute(
                "UPDATE effects SET state=?, evidence=?, updated_at=? WHERE intent_hash=? AND effect_id=?",
                (REQUESTED, f'{{"invalidated": "{reason}"}}', time.time(), intent_hash, effect_id),
            )
            return self.get(intent_hash, effect_id) or {}

    # ---- approvals -----------------------------------------------------
    def record_approval(self, *, intent_hash: str, effect_id: str, approved_by: str,
                        reason: str = "") -> dict:
        """A human said yes, and who. An unattributed approval is not evidence."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO approvals (intent_hash, effect_id, approved_by,"
                " reason, created_at) VALUES (?,?,?,?,?)",
                (intent_hash, effect_id, approved_by, reason, time.time()))
        return self.approval(intent_hash, effect_id) or {}

    def approval(self, intent_hash: str, effect_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approvals WHERE intent_hash=? AND effect_id=?",
                (intent_hash, effect_id)).fetchone()
            return dict(row) if row else None

    def approvals(self, intent_hash: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM approvals WHERE intent_hash=? ORDER BY created_at",
                (intent_hash,)).fetchall()]

    # ---- reads ---------------------------------------------------------
    def mark_post_commit_duplicate(self, intent_hash: str, effect_id: str, *,
                                   evidence: dict) -> dict:
        """A second equivalent object appeared after this effect was committed.

        The effect is not rewritten and the commit is not reversed - the world really
        does hold it. It is flagged so nothing downstream treats the intent as clean.
        """
        return self.transition(intent_hash, effect_id, POST_COMMIT_DUPLICATE,
                               evidence={"reason": "a second equivalent external object "
                                                  "appeared after commit", **evidence})

    def get(self, intent_hash: str, effect_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM effects WHERE intent_hash=? AND effect_id=?", (intent_hash, effect_id)
            ).fetchone()
            return dict(row) if row else None

    def all(self, intent_hash: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM effects WHERE intent_hash=? ORDER BY effect_id", (intent_hash,)
            ).fetchall()
            return [dict(r) for r in rows]

    def may_write(self, intent_hash: str, effect_id: str) -> bool:
        """A duplicate write is forbidden unless the effect is PLANNED or NOT_FOUND."""
        row = self.get(intent_hash, effect_id)
        if row is None:
            return False
        return row["state"] in (PLANNED, NOT_FOUND)

    def record_reconciliation(self, *, intent_hash: str, effect_id: str, ambiguous_state: str,
                              confidence: str, found_existing: bool, strategy: str,
                              detail: dict | None = None) -> None:
        import json

        with self._lock:
            self._conn.execute(
                "INSERT INTO reconciliations (intent_hash, effect_id, ambiguous_state, confidence,"
                " found_existing, strategy, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (intent_hash, effect_id, ambiguous_state, confidence, int(found_existing),
                 strategy, json.dumps(detail or {}), time.time()),
            )

    def reconciliations(self, intent_hash: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reconciliations WHERE intent_hash=? ORDER BY id", (intent_hash,)
            ).fetchall()
            return [dict(r) for r in rows]
