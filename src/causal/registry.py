"""The intent registry: atomic conflict enforcement across concurrent intents.

A partial unique index on conflict_key WHERE status='ACTIVE' is what makes this
a database-level refusal instead of a check-then-act race. Two intents targeting
the same business outcome cannot both be active, however they arrive.

The policy for a collision (this is the part that answers "what if your key is
wrong?"):

  same conflict key + same intent hash   -> IDEMPOTENT   the same intent again
  same conflict key + different hash
      and the holder is ACTIVE           -> CONFLICT     refuse the newcomer
      and the holder is COMMITTED        -> SUPERSEDE    freeze + escalate to a
                                                          human; a reschedule is a
                                                          real request, not an error
"""

from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    intent_id    TEXT PRIMARY KEY,
    conflict_key TEXT NOT NULL,
    intent_hash  TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   REAL NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    resource     TEXT NOT NULL DEFAULT '',
    operation    TEXT NOT NULL DEFAULT '',
    lease_expires_at REAL NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_intent_per_outcome
    ON intents(conflict_key) WHERE status = 'ACTIVE';
"""

REGISTERED = "REGISTERED"
IDEMPOTENT = "IDEMPOTENT"
CONFLICT = "CONFLICT"
SUPERSEDE = "SUPERSEDE"
RESUME = "RESUME"


class Registry:
    def __init__(self, path: str = "causal.db") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, isolation_level=None, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.executescript(SCHEMA)
        # older ledgers predate scope identity; add it rather than refuse to open
        for column, kind in (("resource", "TEXT NOT NULL DEFAULT ''"),
                             ("operation", "TEXT NOT NULL DEFAULT ''"),
                             ("lease_expires_at", "REAL NOT NULL DEFAULT 0")):
            try:
                self._conn.execute(
                    f"ALTER TABLE intents ADD COLUMN {column} {kind}")
            except sqlite3.OperationalError:
                pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def register(self, *, intent_id: str, conflict_key: str, intent_hash: str,
                 lease_seconds: float = 300.0) -> dict:
        """Return {'outcome': ..., 'holder': row|None, 'why': str}.

        Order matters. An identical intent hash anywhere is IDEMPOTENT before
        anything else is considered: re-submitting the same intent is never new
        work, whatever state the original reached.
        """
        with self._lock:
            same_hash = self._conn.execute(
                "SELECT * FROM intents WHERE intent_hash=? ORDER BY created_at LIMIT 1",
                (intent_hash,),
            ).fetchone()
            if same_hash is not None:
                status = same_hash["status"]
                if status in ("ACTIVE", "COMMITTED"):
                    if status == "ACTIVE":
                        # An ACTIVE intent whose worker died holding it must not block the
                        # outcome forever, and a second worker must not simply re-run it
                        # either: it takes the lease over and reconciles what already
                        # exists before writing anything.
                        lease = same_hash["lease_expires_at"]
                        # 0 means expired, not "no lease": the reaper sets exactly 0.
                        if lease < time.time():
                            self._conn.execute(
                                "UPDATE intents SET lease_expires_at=?,"
                                " note='taken over after the previous lease expired'"
                                " WHERE intent_id=?",
                                (time.time() + lease_seconds, same_hash["intent_id"]))
                            return {"outcome": RESUME, "holder": dict(same_hash),
                                    "why": "the previous worker's lease expired, so this worker "
                                           "takes the intent over and reconciles what exists"}
                    return {"outcome": IDEMPOTENT, "holder": dict(same_hash),
                            "why": f"this intent already exists in state {status}"}
                # A previous attempt failed. Replaying it is a RESUME, not a
                # duplicate — unless someone else now holds the outcome.
                other = self._conn.execute(
                    "SELECT intent_id FROM intents WHERE conflict_key=? AND status='ACTIVE'"
                    " AND intent_id<>?", (conflict_key, same_hash["intent_id"]),
                ).fetchone()
                if other is not None:
                    return {"outcome": CONFLICT, "holder": dict(other),
                            "why": "another active intent holds this outcome, so this failed "
                                   "attempt cannot resume"}
                self._conn.execute(
                    "UPDATE intents SET status='ACTIVE', note='resumed after a failed attempt'"
                    " WHERE intent_id=?", (same_hash["intent_id"],))
                return {"outcome": RESUME, "holder": dict(same_hash),
                        "why": f"resuming an intent previously in state {status}"}

            row = self._conn.execute(
                "SELECT * FROM intents WHERE conflict_key=? AND status='ACTIVE'",
                (conflict_key,),
            ).fetchone()

            if row is None:
                # a committed holder for the same outcome?
                done = self._conn.execute(
                    "SELECT * FROM intents WHERE conflict_key=? AND status='COMMITTED'"
                    " ORDER BY created_at DESC LIMIT 1",
                    (conflict_key,),
                ).fetchone()
                if done is not None and done["intent_hash"] != intent_hash:
                    self._insert(intent_id, conflict_key, intent_hash, "FROZEN",
                                 f"supersedes {done['intent_id']}")
                    return {
                        "outcome": SUPERSEDE,
                        "holder": dict(done),
                        "why": "this outcome was already committed with a different scope; "
                               "a change of scope needs human authority, so this intent is frozen",
                    }
                try:
                    self._insert(intent_id, conflict_key, intent_hash, "ACTIVE", "")
                    self._conn.execute(
                        "UPDATE intents SET lease_expires_at=? WHERE intent_id=?",
                        (time.time() + lease_seconds, intent_id))
                    return {"outcome": REGISTERED, "holder": None, "why": "no active intent for this outcome"}
                except sqlite3.IntegrityError:
                    row = self._conn.execute(
                        "SELECT * FROM intents WHERE conflict_key=? AND status='ACTIVE'",
                        (conflict_key,),
                    ).fetchone()

            if row is not None:
                self._insert(intent_id, conflict_key, intent_hash, "BLOCKED",
                             f"conflicts with {row['intent_id']}")
                return {
                    "outcome": CONFLICT,
                    "holder": dict(row),
                    "why": "another active intent targets the same business outcome with a different scope",
                }
            return {"outcome": CONFLICT, "holder": dict(row) if row else None, "why": "unresolved race"}

    def expire_lease(self, intent_id: str) -> None:
        """Release a dead worker's hold so another worker can take the intent over.

        An operator or a reaper does this after noticing a worker died mid-job. It is
        deliberately a separate act rather than something a competing worker can do to
        itself, or every contending worker would simply seize whatever it wanted.
        """
        with self._lock:
            self._conn.execute("UPDATE intents SET lease_expires_at=0 WHERE intent_id=?",
                               (intent_id,))

    def note_scope_identity(self, intent_id: str, *, resource: str, operation: str) -> None:
        """Record what real-world resource this intent contends for."""
        with self._lock:
            self._conn.execute("UPDATE intents SET resource=?, operation=? WHERE intent_id=?",
                               (resource, operation, intent_id))

    def exclusive_holder(self, *, resource: str, operation: str,
                         exclude_intent_id: str = "") -> dict | None:
        """An ACTIVE intent on the same resource whose operation cannot coexist.

        This is why duplicate identity and contradictory intent are two mechanisms:
        they produce different conflict keys, so the registry's unique index cannot see
        the second case at all.
        """
        from .binding import exclusivity_conflict

        if not resource:
            return None
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM intents WHERE resource=? AND status='ACTIVE' AND intent_id<>?",
                (resource, exclude_intent_id),
            ).fetchall()
        for row in rows:
            if exclusivity_conflict(operation, row["operation"] or "default"):
                return dict(row)
        return None

    def set_status(self, intent_id: str, status: str, note: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE intents SET status=?, note=? WHERE intent_id=?",
                (status, note, intent_id),
            )

    def get(self, intent_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            return dict(row) if row else None

    def list_all(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM intents ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def active_for(self, conflict_key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM intents WHERE conflict_key=? AND status='ACTIVE'",
                (conflict_key,),
            ).fetchone()
            return dict(row) if row else None

    def _insert(self, intent_id: str, conflict_key: str, intent_hash: str, status: str, note: str) -> None:
        self._conn.execute(
            "INSERT INTO intents (intent_id, conflict_key, intent_hash, status, created_at, note)"
            " VALUES (?,?,?,?,?,?)",
            (intent_id, conflict_key, intent_hash, status, time.time(), note),
        )
