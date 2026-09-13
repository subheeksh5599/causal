"""Append-only audit chain.

Every state transition that matters writes one row here. This is the judge-facing
asset: the whole causal chain of an intent, in order, with reasons.

Hash chaining gives tamper-evidence: each row carries the hash of the previous
row plus its own payload, so a deleted or edited row breaks the chain.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id     TEXT NOT NULL,
    intent_hash   TEXT NOT NULL DEFAULT '',
    event_type    TEXT NOT NULL,
    previous_state TEXT NOT NULL DEFAULT '',
    new_state     TEXT NOT NULL DEFAULT '',
    reason        TEXT NOT NULL DEFAULT '',
    actor         TEXT NOT NULL DEFAULT 'system',
    metadata      TEXT NOT NULL DEFAULT '{}',
    prev_hash     TEXT NOT NULL DEFAULT '',
    event_hash    TEXT NOT NULL,
    created_at    REAL NOT NULL
);
"""

INTENT_CREATED = "INTENT_CREATED"
EVIDENCE_ACCEPTED = "EVIDENCE_ACCEPTED"
EVIDENCE_REJECTED = "EVIDENCE_REJECTED"
AUTHORITY_FROZEN = "AUTHORITY_FROZEN"
CONFLICT_DETECTED = "CONFLICT_DETECTED"
CONFLICT_CLEAR = "CONFLICT_CLEAR"
SCOPE_REJECTED = "SCOPE_REJECTED"
EFFECT_REQUESTED = "EFFECT_REQUESTED"
EFFECT_REJECTED = "EFFECT_REJECTED"
EFFECTED = "EFFECTED"
EFFECT_UNKNOWN = "EFFECT_UNKNOWN"
VERIFICATION_STARTED = "VERIFICATION_STARTED"
VERIFIED = "VERIFIED"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
RECONCILIATION_RESOLVED = "RECONCILIATION_RESOLVED"
AMBIGUOUS_ESCALATED = "AMBIGUOUS_ESCALATED"
COMMIT_EVALUATED = "COMMIT_EVALUATED"
COMMITTED = "COMMITTED"
COMMIT_REJECTED = "COMMIT_REJECTED"
INTENT_REFUSED = "INTENT_REFUSED"
BINDING_ATTEMPTED = "BINDING_ATTEMPTED"
BOUND = "BOUND"
NOT_PROVABLE = "NOT_PROVABLE"
DUPLICATE_DETECTED = "DUPLICATE_DETECTED"
EXCLUSIVITY_BLOCKED = "EXCLUSIVITY_BLOCKED"
POST_COMMIT_DUPLICATE = "POST_COMMIT_DUPLICATE"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVAL_GRANTED = "APPROVAL_GRANTED"
FAULT_INJECTED = "FAULT_INJECTED"


class Audit:
    def __init__(self, path: str = "causal.db") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record(self, *, intent_id: str, event_type: str, intent_hash: str = "",
               previous_state: str = "", new_state: str = "", reason: str = "",
               actor: str = "system", metadata: dict | None = None) -> dict:
        with self._lock:
            tail = self._conn.execute(
                "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev = tail["event_hash"] if tail else ""
            meta = json.dumps(metadata or {}, sort_keys=True)
            ts = time.time()
            blob = json.dumps(
                {"intent_id": intent_id, "intent_hash": intent_hash, "event_type": event_type,
                 "previous_state": previous_state, "new_state": new_state, "reason": reason,
                 "actor": actor, "metadata": meta, "prev_hash": prev, "ts": ts},
                sort_keys=True,
            )
            event_hash = hashlib.sha256(blob.encode()).hexdigest()
            self._conn.execute(
                "INSERT INTO audit_events (intent_id, intent_hash, event_type, previous_state,"
                " new_state, reason, actor, metadata, prev_hash, event_hash, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (intent_id, intent_hash, event_type, previous_state, new_state, reason,
                 actor, meta, prev, event_hash, ts),
            )
            return {"event_type": event_type, "new_state": new_state, "event_hash": event_hash}

    def timeline(self, intent_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, event_type, previous_state, new_state, reason, actor, metadata,"
                " prev_hash, event_hash, created_at FROM audit_events WHERE intent_id=? ORDER BY id",
                (intent_id,),
            ).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            try:                       # consumers get the payload, not a JSON string
                row["metadata"] = json.loads(row["metadata"] or "{}")
            except Exception:
                row["metadata"] = {}
            out.append(row)
        return out

    def verify_chain(self, intent_id: str) -> bool:
        """Verify the audit log.

        The chain is global: every row links to the row before it in the whole
        log, not just in its own intent. So there are two distinct claims:

          * intent-scoped  - each row's hash still matches its own content, so no
                             row of this intent has been edited. Ordering across
                             intents cannot be checked from a subset.
          * whole log      - additionally, every row's stored prev_hash equals the
                             previous row's hash, so no row has been removed.

        Passing an intent_id asks the first question; passing nothing asks both.
        An earlier version asked the second question with subset data and always
        reported False.
        """
        with self._lock:
            sql = "SELECT * FROM audit_events"
            params: tuple = ()
            if intent_id:
                sql += " WHERE intent_id=?"
                params = (intent_id,)
            rows = [dict(r) for r in self._conn.execute(sql + " ORDER BY id", params).fetchall()]
        prev = ""
        for r in rows:
            blob = json.dumps(
                {"intent_id": r["intent_id"], "intent_hash": r["intent_hash"],
                 "event_type": r["event_type"], "previous_state": r["previous_state"],
                 "new_state": r["new_state"], "reason": r["reason"], "actor": r["actor"],
                 "metadata": r["metadata"], "prev_hash": r["prev_hash"], "ts": r["created_at"]},
                sort_keys=True,
            )
            if hashlib.sha256(blob.encode()).hexdigest() != r["event_hash"]:
                return False
            if intent_id is None and r["prev_hash"] != prev:
                return False
            prev = r["event_hash"]
        return True
