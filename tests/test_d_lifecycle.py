"""Groups F (effect ledger), G (independent verification), H (reconciliation)
- tests 076-120."""

from __future__ import annotations

import json
import time

import pytest

from causal import EffectLedger, IllegalTransition
from causal import ledger as L
from causal.audit import RECONCILIATION_STARTED, RECONCILIATION_RESOLVED, VERIFIED
from causal.apps import TransientError
from causal.policy import can_execute, can_retry
from causal.reconcile import AMBIGUOUS, EXACT, LIKELY, NOT_FOUND, action_for, reconcile

from .conftest import make_intent, seed_approval


def _plan(ledger, h="h1", eid="E-1", app="calendar"):
    ledger.plan(intent_hash=h, intent_id="A", effect_id=eid, app=app,
                operation="CREATE_EVENT", idempotency_key=f"{h}::{eid}")


def _evidence(ledger, h="h1", eid="E-1"):
    row = ledger.get(h, eid)
    return json.loads(row["evidence"] or "{}")


# --------------------------------------------------------------------------
# Group F - effect ledger
# --------------------------------------------------------------------------

def test_076_a_new_effect_starts_planned(stack):
    _plan(stack.ledger)
    assert stack.ledger.get("h1", "E-1")["state"] == L.PLANNED


@pytest.mark.parametrize("seq,expected", [
    ([L.REQUESTED, L.EFFECTED, L.VERIFYING, L.VERIFIED], L.VERIFIED),    # 077-080
    ([L.REQUESTED, L.UNKNOWN, L.RECONCILING, L.NOT_FOUND, L.REQUESTED], L.REQUESTED),
])
def test_077_to_080_legal_transitions_walk(stack, seq, expected):
    _plan(stack.ledger)
    for state in seq:
        stack.ledger.transition("h1", "E-1", state)
    assert stack.ledger.get("h1", "E-1")["state"] == expected


@pytest.mark.parametrize("path", [
    [L.REQUESTED, L.EFFECTED, L.VERIFYING, L.VERIFIED, L.REQUESTED],   # 083 verified cannot go back
    [L.REQUESTED, L.EFFECTED, L.VERIFYING, L.PLANNED],                # 081 no jumping backwards
    [L.REQUESTED, L.EFFECTED, L.ESCALATED],                           # 082 not a legal edge
])
def test_081_to_083_illegal_transitions_are_refused(stack, path):
    _plan(stack.ledger)
    with pytest.raises(IllegalTransition):
        for state in path:
            stack.ledger.transition("h1", "E-1", state)


def test_084_verified_can_only_leave_through_an_explicit_invalidation(stack):
    _plan(stack.ledger)
    for state in (L.REQUESTED, L.EFFECTED, L.VERIFYING, L.VERIFIED):
        stack.ledger.transition("h1", "E-1", state)
    out = stack.ledger.invalidate_verified("h1", "E-1", "external object tampered with")
    assert out["state"] == L.REQUESTED
    assert "tampered" in out["evidence"]


def test_085_086_may_write_only_from_planned_or_not_found(stack):
    _plan(stack.ledger)
    assert stack.ledger.may_write("h1", "E-1") is True
    for state in (L.REQUESTED, L.EFFECTED, L.VERIFYING, L.VERIFIED):
        stack.ledger.transition("h1", "E-1", state)
    assert stack.ledger.may_write("h1", "E-1") is False
    stack.ledger.invalidate_verified("h1", "E-1", "test")
    stack.ledger.transition("h1", "E-1", L.UNKNOWN)
    stack.ledger.transition("h1", "E-1", L.RECONCILING)
    stack.ledger.transition("h1", "E-1", L.NOT_FOUND)
    assert stack.ledger.may_write("h1", "E-1") is True


def test_087_a_verified_effect_stores_the_external_object_id(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    cal = stack.ledger.get(intent.intent_hash, "CALENDAR-01")
    assert cal["state"] == L.VERIFIED and cal["external_id"].startswith("evt-")


def test_088a_attempts_count_write_attempts_after_a_lost_response(stack):
    seed_approval(stack.apps)
    stack.apps.slack.fail_after(1)                       # landed, response lost
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    assert stack.ledger.get(intent.intent_hash, "SLACK-01")["attempts"] == 1


def test_088b_attempts_count_write_attempts_when_nothing_landed(stack):
    seed_approval(stack.apps)
    stack.apps.slack.fail_before(1)                      # never landed, so a retry is right
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    assert stack.ledger.get(intent.intent_hash, "SLACK-01")["attempts"] == 2


def test_089_effect_transitions_are_audit_logged(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    types = [e["event_type"] for e in stack.audit.timeline(intent.intent_id)]
    assert "EFFECT_REQUESTED" in types and "EFFECTED" in types and VERIFIED in types


def test_the_seven_sequences_are_reproducible(tmp_path):
    """Run the whole scenario set twice against separate ledgers and demand the
    same summary. A demo whose numbers change on a second press is not evidence."""
    from causal import scenarios

    summaries = []
    for run in (1, 2):
        s = scenarios.fresh_stack("LOCAL", str(tmp_path / f"run{run}.db"))
        try:
            reports = [scenarios.run(name, s) for name, _ in scenarios.SEQUENCES]
            summaries.append({
                "committed": sum(1 for r in reports if r["result"].get("committed")),
                "refused": sum(1 for r in reports if r["result"].get("status") == "REFUSED"),
                "reconciled": sum(len(r["result"].get("reconciliations") or []) for r in reports),
                "wrote": [r["wrote"] for r in reports],
                "statuses": [r["result"].get("status") for r in reports],
                "chain_linked": all(r["chain_linked"] for r in reports),
                "audit_intact": all(r["audit_intact"] for r in reports),
            })
        finally:
            s.close()

    assert summaries[0] == summaries[1]
    # Four sequences commit by design: intended, timeout_after_write, crash_recovery
    # (a takeover that binds an existing effect) and duplicate_after_commit (which
    # commits and is then flagged). The rest are refusals, idempotent returns or
    # blocks. If this number changes, either a sequence changed meaning or the
    # commit rule moved — both worth a look.
    assert summaries[0]["committed"] == 4
    assert summaries[0]["refused"] == 4
    assert summaries[0]["chain_linked"] is True
    assert summaries[0]["audit_intact"] is True


def test_090_reconciliation_is_persisted(stack):
    seed_approval(stack.apps)
    stack.apps.calendar.fail_after(1)
    intent = make_intent(intent_id="C-A")
    stack.engine.run(intent, evidence_message_id="msg-1")
    rows = stack.ledger.reconciliations(intent.intent_hash)
    assert rows and rows[0]["confidence"] == EXACT and rows[0]["found_existing"] == 1


# --------------------------------------------------------------------------
# Group G - independent verification
# --------------------------------------------------------------------------

def _artifact(**over):
    base = {"id": "evt-9999", "title": "Acme Implementation Kickoff",
            "start_iso": "2026-09-15T15:00", "attendees": ["#engineering"],
            "intent_hash": "not-supplied-by-this-helper"}
    base.update(over)
    return base


def _run_with_calendar_read(stack, artifacts_fn, *, intent=None):
    seed_approval(stack.apps)
    intent = intent or make_intent()
    _plan_engine = stack.engine
    original = stack.apps.calendar.list_events

    def patched(**kw):
        return artifacts_fn(intent, original, kw)

    stack.apps.calendar.list_events = patched
    try:
        return stack.engine.run(intent, evidence_message_id="msg-1"), intent
    finally:
        stack.apps.calendar.list_events = original


def test_091_092_the_write_is_followed_by_an_independent_read(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    ev = _evidence(stack.ledger, intent.intent_hash, "CALENDAR-01")
    assert ev.get("violations") == []                      # structured checks, not a bare boolean
    assert stack.ledger.get(intent.intent_hash, "LINEAR-01")["state"] == L.VERIFIED


def test_093_verification_uses_a_different_call_than_the_write(stack):
    """The verifier is handed nothing from the write. Prove it by lying on the write."""
    seed_approval(stack.apps)
    intent = make_intent()
    real_insert = stack.apps.calendar.insert_event

    def lying_insert(**kw):
        real_insert(**kw)
        return {"id": "WRONG-ID-FROM-THE-WRITE-RESPONSE"}

    stack.apps.calendar.insert_event = lying_insert
    try:
        result = stack.engine.run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.calendar.insert_event = real_insert

    cal = stack.ledger.get(intent.intent_hash, "CALENDAR-01")
    assert cal["state"] == L.VERIFIED
    assert cal["external_id"] != "WRONG-ID-FROM-THE-WRITE-RESPONSE"
    assert result.committed is True


def test_095_object_absent_on_read_back_fails_verification(stack):
    result, _ = _run_with_calendar_read(stack, lambda i, o, kw: [])
    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "VERIFICATION_FAILED"
    assert result.committed is False


@pytest.mark.parametrize("over,violation", [
    ({"title": "Acme Implementation Migration"}, "title"),        # 096 title mismatch
    ({"title": "Contoso Implementation Kickoff"}, "title"),       # 097 customer mismatch
    ({"start_iso": "2026-09-16T16:00"}, "start"),                 # 099 time mismatch
    ({"attendees": ["intruder@example.com"]}, "attendees"),       # 101 attendee mismatch
    ({"intent_hash": "not-this-intent"}, "hash"),                 # 102 wrong intent hash
])
def test_096_to_102_a_wrong_artifact_fails_verification(stack, over, violation):
    def artifacts(intent, original, kw):
        merged = {"intent_hash": intent.intent_hash}
        merged.update(over)                     # an override may replace the hash itself
        return [_artifact(**merged)]

    result, intent = _run_with_calendar_read(stack, artifacts)
    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "VERIFICATION_FAILED"
    assert result.committed is False


def test_098_project_mismatch_on_the_task_is_caught(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    real = stack.apps.linear.list_tasks
    stack.apps.linear.list_tasks = lambda **kw: [
        {"id": "SUB-1", "project": "Migration", "title": "Acme Implementation Kickoff",
         "intent_hash": intent.intent_hash}]
    try:
        result = stack.engine.run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.linear.list_tasks = real
    lin = next(e for e in result.effects if e["effect_id"] == "LINEAR-01")
    assert lin["state"] == "VERIFICATION_FAILED"


def test_103_an_object_modified_after_the_write_is_detected(stack):
    def artifacts(intent, original, kw):
        real = original(**kw)
        if not real:
            return []
        tampered = dict(real[0])
        tampered["title"] = "Acme Implementation Kickoff (edited by hand)"
        return [tampered]

    result, _ = _run_with_calendar_read(stack, artifacts)
    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "VERIFICATION_FAILED" and result.committed is False


def test_104_an_object_deleted_after_the_write_is_detected(stack):
    def artifacts(intent, original, kw):
        original(**kw)          # it landed
        return []               # and then it vanished

    result, _ = _run_with_calendar_read(stack, artifacts)
    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "VERIFICATION_FAILED"


def test_105_verification_result_carries_structured_checks(stack):
    result, intent = _run_with_calendar_read(
        stack, lambda i, o, kw: [_artifact(title="Wrong Title", intent_hash=i.intent_hash)])
    cal = stack.ledger.get(intent.intent_hash, "CALENDAR-01")
    ev = json.loads(cal["evidence"])
    assert ev["ok"] is False and isinstance(ev["violations"], list) and ev["violations"]


# --------------------------------------------------------------------------
# Group H - reconciliation
# --------------------------------------------------------------------------

def _cand(**over):
    base = {"id": "evt-1", "intent_hash": "h1", "subject": "Acme", "operation": "Kickoff"}
    base.update(over)
    return base


def test_106_107_unknown_is_reconciled_not_retried(stack):
    seed_approval(stack.apps)
    stack.apps.calendar.fail_after(1)
    intent = make_intent(intent_id="C-A")
    stack.engine.run(intent, evidence_message_id="msg-1")
    types = [e["event_type"] for e in stack.audit.timeline(intent.intent_id)]
    assert RECONCILIATION_STARTED in types and RECONCILIATION_RESOLVED in types
    assert stack.ledger.get(intent.intent_hash, "CALENDAR-01")["attempts"] == 1


def test_108_109_110_exact_likely_and_not_found(stack):
    expected = {"intent_hash": "h1", "subject": "Acme", "operation": "Kickoff"}
    exact = reconcile([_cand()], expected)
    assert exact.confidence == EXACT and exact.found and exact.object_id == "evt-1"

    likely = reconcile([_cand(intent_hash="other")], expected)
    assert likely.confidence == LIKELY and likely.found

    assert reconcile([], expected).confidence == NOT_FOUND


def test_112_113_multiple_matches_are_ambiguous(stack):
    expected = {"intent_hash": "h1", "subject": "Acme", "operation": "Kickoff"}
    assert reconcile([_cand(), _cand(id="evt-2")], expected).confidence == AMBIGUOUS
    assert reconcile([_cand(intent_hash="x", id="e1"), _cand(intent_hash="y", id="e2")],
                     expected).confidence == AMBIGUOUS


def test_111_114_115_116_the_retry_policy_is_explicit(stack):
    assert can_retry(L.NOT_FOUND).allow is True
    assert can_retry(L.UNKNOWN).allow is False
    assert can_retry(L.VERIFIED).allow is False
    assert can_retry(L.AMBIGUOUS).allow is False
    assert action_for(EXACT) == "VERIFY" and action_for(AMBIGUOUS) == "ESCALATE"


def test_117_an_ambiguous_effect_cannot_be_executed_again(stack):
    intent = make_intent()
    intent.freeze()
    spec = intent.effects[0]
    d = can_execute(intent, spec, effect_state=L.AMBIGUOUS, now_ts=time.time(),
                    authority_frozen=True, conflict_clear=True)
    assert d.allow is False and any("AMBIGUOUS" in r for r in d.reasons)


def test_118_119_120_reconciliation_is_idempotent_and_prevents_a_duplicate(stack):
    seed_approval(stack.apps)
    stack.apps.calendar.fail_after(1)
    intent = make_intent(intent_id="C-A")
    stack.engine.run(intent, evidence_message_id="msg-1")
    result = stack.engine.run(intent, evidence_message_id="msg-1")     # replay
    assert len(stack.apps.calendar.world.events) == 1
    assert len(stack.ledger.reconciliations(intent.intent_hash)) >= 1
    assert result.committed is True or result.status == "IDEMPOTENT"
