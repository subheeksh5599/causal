"""The sequences, as callable functions.

The CLI harness (`scripts/demo.py`) and the console API (`causal/api.py`) both
drive these, so what a judge clicks is the same code path the tests and the
harness exercise. One implementation, three ways to reach it.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from . import audit as A
from . import binding
from . import plain
from .apps import Apps
from .audit import Audit
from .engine import Causal
from .evidence_sink import OutcomeStore
from .intent import Intent, Scope, conflict_key_for, EffectSpec
from .ledger import EffectLedger
from .registry import Registry
def _fixture_clock() -> tuple[str, str, str, str]:
    """The demo world's dates, computed from a real date rather than frozen.

    The base is the Monday of the current week, so the week's Tuesday is unambiguous and
    the seeded approval and the intent it authorises always agree. None of these is a
    literal: a console opened next year shows next year's dates. The engine takes the
    clock as a parameter (`reference_date_iso`, `now_ts`) and never reaches for the wall
    clock inside a decision, so pinning a run for a reproducible quote is one env var:

        CAUSAL_REFERENCE_DATE=2026-09-14 uv run python scripts/demo.py

    Staleness is measured from each message's own timestamp, not from this date, so
    changing the base cannot make an approval look fresher than it is.
    """
    pinned = os.environ.get("CAUSAL_REFERENCE_DATE", "").strip()
    today = date.fromisoformat(pinned) if pinned else date.today()
    monday = today - timedelta(days=today.weekday())
    tuesday = monday + timedelta(days=1)
    wednesday = monday + timedelta(days=2)
    return (monday.isoformat(), f"{tuesday.isoformat()}T15:00",
            f"{tuesday.isoformat()}T16:00", f"{wednesday.isoformat()}T16:00")
CONTACT = "buyer@acme.example"
REFERENCE_DATE = "2026-09-14"
TUESDAY = "2026-09-15T15:00"
TUESDAY_LATE = "2026-09-15T16:00"
WEDNESDAY = "2026-09-16T16:00"
SEQUENCES = [
    ("intended", "The intended path"),
    ("timeout_after_write", "Timeout AFTER the write — reconciled, never retried blind"),
    ("conflict", "Two intents, one business outcome — the second is refused"),
    ("unauthorized_success", "The API succeeds and the action still fails"),
    ("missing_evidence", "Evidence that does not exist — nothing executes"),
    ("lying_model", "A model claiming total success changes nothing"),
    ("crash_recovery", "Worker A dies holding the job — worker B takes over, no duplicate"),
    ("duplicate_intent", "The same intent arrives twice while the lease is live"),
    ("duplicate_before_commit", "An equivalent effect already exists — refuses to commit"),
    ("duplicate_after_commit", "A duplicate appears after the commit — flagged, not undone"),
    ("absence_not_provable", "Where absence cannot be proven, it never retries"),
    ("counter_intent", "Renew and cancel contend for one contract"),
    ("awaiting_signoff", "Outbound waits for a person; internal effects do not"),
]
ACME_PEOPLE = ("dana.reyes@acme.example", "sam.okafor@acme.example")
def build_binding_intent(customer: str, project: str, start_iso: str, *, intent_id: str,
                         event: str = "Kickoff", operation: str = "", resource: str = "",
                         recipients: tuple[str, ...] = ACME_PEOPLE) -> Intent:
    """An intent that names the real-world resource it contends for.

    `resource`/`operation` are what the authored exclusivity relation is checked
    against; they are deliberately not part of the conflict key.
    """
    scope = Scope(customer=customer, project=project, event=event, start_iso=start_iso,
                  recipients=recipients,
                  allowed_apps=("gmail", "calendar", "linear", "slack"),
                  resource=resource, operation=operation)
    effects = (
        EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT"),
        EffectSpec("LINEAR-01", "linear", "CREATE_ISSUE"),
        EffectSpec("SLACK-01", "slack", "POST_MESSAGE"),
    )
    return Intent(intent_id=intent_id, request=f"{customer} approved the {project}.",
                  scope=scope,
                  authority={"approval": "gmail", "time": "gmail", "meeting": "calendar",
                             "work": "linear"},
                  effects=effects, conflict_key=conflict_key_for(scope))


@dataclass
