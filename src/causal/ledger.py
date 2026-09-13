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
