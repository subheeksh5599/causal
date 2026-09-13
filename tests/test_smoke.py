"""Smoke tests: does the protocol actually run.

These exercise the three sequences the demo is built on, before the full suite
is written, because a suite built on a broken engine proves nothing.
"""

from __future__ import annotations

import pytest

from causal import EffectSpec
from causal.apps import TransientError

from .conftest import make_intent, seed_approval


def test_happy_path_commits_only_after_independent_verification(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    result = stack.engine.run(intent, evidence_message_id="msg-1")

    assert result.committed is True
    assert result.status == "COMMITTED"
    states = {e["effect_id"]: e["state"] for e in result.effects}
    assert states == {"CALENDAR-01": "VERIFIED", "LINEAR-01": "VERIFIED", "SLACK-01": "VERIFIED"}
    # ground truth, read directly
    assert len(stack.apps.calendar.world.events) == 1
    assert len(stack.apps.linear.world.tasks) == 1
    assert len(stack.apps.slack.world.messages) == 1
    assert stack.audit.verify_chain(intent.intent_id)


def test_timeout_after_write_reconciles_without_duplicate(stack):
    seed_approval(stack.apps)
    stack.apps.slack.fail_after(1)     # the message lands, we never see the response
    intent = make_intent()
    result = stack.engine.run(intent, evidence_message_id="msg-1")

    slack = next(e for e in result.effects if e["effect_id"] == "SLACK-01")
    assert result.committed is True
    assert slack["state"] == "VERIFIED"
    assert len(stack.apps.slack.world.messages) == 1     # exactly one, not two
    assert slack["attempts"] == 1                        # one write; reconciliation did the rest
    assert result.reconciliations and result.reconciliations[0]["confidence"] == "EXACT"


def test_timeout_before_write_retries_after_not_found(stack):
    seed_approval(stack.apps)
    stack.apps.linear.fail_before(1)   # nothing landed, so a retry is correct
    intent = make_intent()
    result = stack.engine.run(intent, evidence_message_id="msg-1")

    linear = next(e for e in result.effects if e["effect_id"] == "LINEAR-01")
    assert result.committed is True
    assert linear["attempts"] == 2
    assert len(stack.apps.linear.world.tasks) == 1


def test_active_intent_blocks_a_new_one(stack):
    """An intent that is genuinely still active holds the business outcome."""
    seed_approval(stack.apps)
    seed_approval(stack.apps, body="Acme approved the implementation. Kickoff 2026-09-15T16:00.",
                  message_id="msg-2")
    a = make_intent(intent_id="C-A")
    a.freeze()
    stack.registry.register(intent_id=a.intent_id, conflict_key=a.conflict_key,
                            intent_hash=a.intent_hash)          # A holds the outcome, still active

    b = make_intent(intent_id="C-B", start_iso="2026-09-15T16:00")
    second = stack.engine.run(b, evidence_message_id="msg-2")

    assert second.committed is False
    assert second.refusal_code == "CONFLICT"
    assert len(stack.apps.calendar.world.events) == 0


def test_concurrent_race_commits_exactly_one(stack, monkeypatch):
    """Two threads, same business outcome. Exactly one may commit."""
    import threading

    seed_approval(stack.apps)
    seed_approval(stack.apps, body="Acme approved the implementation. Kickoff 2026-09-15T16:00.",
                  message_id="msg-2")
    results: list = []
    lock = threading.Lock()

    def go(intent_id: str, start_iso: str, msg: str) -> None:
        r = stack.engine.run(make_intent(intent_id=intent_id, start_iso=start_iso),
                             evidence_message_id=msg)
        with lock:
            results.append(r)

    threads = [
        threading.Thread(target=go, args=("C-A", "2026-09-15T15:00", "msg-1")),
        threading.Thread(target=go, args=("C-B", "2026-09-15T16:00", "msg-2")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r.committed) == 1
    assert len(stack.apps.calendar.world.events) == 1


def test_unauthorized_success_is_rejected(stack):
    """The API succeeds; the business postcondition does not hold.

    The model proposed a notification that contradicts the frozen scope. Slack
    accepts it happily. CAUSAL must refuse to call the effect verified.
    """
    seed_approval(stack.apps)
    intent = make_intent(slack_text="Acme Implementation Kickoff moved to Wednesday 16:00")
    result = stack.engine.run(intent, evidence_message_id="msg-1")

    slack = next(e for e in result.effects if e["effect_id"] == "SLACK-01")
    assert slack["state"] == "VERIFICATION_FAILED"
    assert result.committed is False
    assert result.refusal_code == "NOT_COMMITTED"
    assert len(stack.apps.slack.world.messages) == 1        # the write happened, once
    # and the other two effects were still verified — no partial commit, no false success
    states = {e["effect_id"]: e["state"] for e in result.effects}
    assert states["CALENDAR-01"] == "VERIFIED" and states["LINEAR-01"] == "VERIFIED"


def test_missing_evidence_writes_nothing(stack):
    intent = make_intent()          # no approval seeded
    result = stack.engine.run(intent, evidence_message_id="msg-absent")
    assert result.refusal_code == "EVIDENCE_MISSING"
    assert (len(stack.apps.calendar.world.events), len(stack.apps.linear.world.tasks),
            len(stack.apps.slack.world.messages)) == (0, 0, 0)


def test_authority_mismatch_writes_nothing(stack):
    seed_approval(stack.apps, body="Acme approved the implementation. Kickoff 2026-09-15T16:00.")
    intent = make_intent(start_iso="2026-09-15T15:00")   # model moved the time
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.refusal_code == "AUTHORITY_MISMATCH"
    assert len(stack.apps.calendar.world.events) == 0


def test_out_of_scope_recipient_writes_nothing(stack):
    seed_approval(stack.apps)
    intent = make_intent(effects=(
        EffectSpec("SLACK-01", "slack", "POST_MESSAGE", target="sales@example"),
    ))
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.refusal_code == "OUT_OF_SCOPE"
    assert len(stack.apps.slack.world.messages) == 0


def test_stale_authorization_writes_nothing(stack):
    seed_approval(stack.apps)
    intent = make_intent(expires_at_ts=1.0)     # long in the past
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.refusal_code == "STALE_AUTHORIZATION"
    assert len(stack.apps.calendar.world.events) == 0
