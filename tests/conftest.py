"""Shared fixtures.

Every test runs against a real sqlite database and real backend state
transitions. Only the network boundary is replaced with in-process services.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from causal.apps import Apps
from causal.audit import Audit
from causal.engine import Causal
from causal.evidence_sink import OutcomeStore
from causal.intent import EffectSpec, Intent, Scope, conflict_key_for
from causal.ledger import EffectLedger
from causal.registry import Registry

CONTACT = "buyer@acme.example"
REFERENCE_DATE = "2026-09-14"          # a Monday; "Tuesday at 3 PM" resolves to 2026-09-15
START_ISO = "2026-09-15T15:00"
DEFAULT_BODY = f"Acme approved the implementation. Kickoff {START_ISO}."


@pytest.fixture
def stack(tmp_path):
    path = str(tmp_path / "causal.db")
    apps = Apps.local()
    registry, ledger, audit, outcomes = Registry(path), EffectLedger(path), Audit(path), OutcomeStore(path)
    engine = Causal(apps, registry, ledger, audit, outcomes,
                    known_contacts={CONTACT}, reference_date_iso=REFERENCE_DATE)
    s = SimpleNamespace(apps=apps, registry=registry, ledger=ledger, audit=audit,
                        outcomes=outcomes, engine=engine, path=path)
    yield s
    for obj in (registry, ledger, audit, outcomes):
        obj.close()


def seed_approval(apps, *, body: str = DEFAULT_BODY, sender: str = CONTACT,
                  message_id: str = "msg-1", timestamp: float | None = None) -> str:
    apps.mail.seed_message(message_id, sender=sender, body=body, timestamp=timestamp)
    return message_id


def make_intent(*, customer="Acme", project="Implementation", event="Kickoff",
                start_iso=START_ISO, recipients=("#engineering",),
                allowed_apps=("gmail", "calendar", "linear", "slack"),
                effects=None, intent_id="C-001", expires_at_ts=None,
                approval_max_age_s=None, authority=None, slack_text=None) -> Intent:
    scope = Scope(customer=customer, project=project, event=event, start_iso=start_iso,
                  recipients=tuple(recipients), allowed_apps=tuple(allowed_apps))
    if effects is None:
        slack = EffectSpec("SLACK-01", "slack", "POST_MESSAGE", target="#engineering",
                           payload=(("text", slack_text),) if slack_text else ())
        effects = (
            EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT"),
            EffectSpec("LINEAR-01", "linear", "CREATE_ISSUE"),
            slack,
        )
    return Intent(
        intent_id=intent_id, request="Acme approved the implementation.",
        scope=scope,
        authority=authority or {"approval": "gmail", "time": "gmail", "meeting": "calendar",
                               "work": "linear", "notification": "slack"},
        effects=tuple(effects), conflict_key=conflict_key_for(scope),
        expires_at_ts=expires_at_ts, approval_max_age_s=approval_max_age_s,
    )
