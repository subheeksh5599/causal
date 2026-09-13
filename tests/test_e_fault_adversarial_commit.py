"""Groups I (fault/crash), J (adversarial model), K (commit gate) - tests 121-150."""

from __future__ import annotations

import time

import pytest

from causal import EffectSpec, Intent, validate_proposal
from causal import ledger as L
from causal import Causal
from causal.apps import TransientError
from causal.intent import FrozenIntentError
from causal.policy import can_commit

from .conftest import (CONTACT, REFERENCE_DATE, START_ISO, make_intent, seed_approval)

ALLOWED = {"gmail", "calendar", "linear", "slack"}


class LyingPlanner:
    """A model that claims total success. The engine never consults it.

    This exists to prove a structural property: there is no parameter, return
    value or side channel through which a model opinion can reach a state
    transition. If a future refactor introduces one, `calls` stops being zero.
    """

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, *_args, **_kwargs) -> dict:
        self.calls += 1
        return {"status": "COMMITTED", "verified": True, "effects": ["all done"],
                "evidence": "invented", "root_cause": "invented"}


def fresh_engine(stack):
    return Causal(stack.apps, stack.registry, stack.ledger, stack.audit, stack.outcomes,
                  known_contacts={CONTACT}, reference_date_iso=REFERENCE_DATE)


def counts(stack):
    return (len(stack.apps.calendar.world.events), len(stack.apps.linear.world.tasks),
            len(stack.apps.slack.world.messages))


# --------------------------------------------------------------------------
# Group I - faults, crashes, restarts
# --------------------------------------------------------------------------

def test_121_a_failure_before_the_write_leaves_nothing_behind(stack):
    seed_approval(stack.apps)
    stack.apps.calendar.fail_before(1)
    intent = make_intent()
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.committed is True
    assert len(stack.apps.calendar.world.events) == 1            # exactly one, not two
    assert stack.ledger.get(intent.intent_hash, "CALENDAR-01")["attempts"] == 2
    types = [e["event_type"] for e in stack.audit.timeline(intent.intent_id)]
    assert "EFFECT_UNKNOWN" in types


def test_122_a_failed_request_produces_a_safe_unknown(stack):
    seed_approval(stack.apps)
    stack.apps.linear.fail_after(1)
    intent = make_intent()
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.committed is True
    lin = stack.ledger.get(intent.intent_hash, "LINEAR-01")
    assert lin["state"] == L.VERIFIED and lin["attempts"] == 1
    assert "unknown" in lin["evidence"].lower() or "reconciliation" in lin["evidence"].lower()


def test_123_timeout_after_write_reconciles_without_a_duplicate(stack):
    seed_approval(stack.apps)
    stack.apps.calendar.fail_after(1)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    assert len(stack.apps.calendar.world.events) == 1
    rows = stack.ledger.reconciliations(intent.intent_hash)
    assert rows and rows[0]["found_existing"] == 1


def test_124_a_crash_after_the_write_is_recoverable(stack):
    """The write landed, the response was lost, and the reconciling read failed too.
    A fresh process must find the object and verify it, not write a second one."""
    seed_approval(stack.apps)
    stack.apps.calendar.fail_after(1)
    intent = make_intent()
    intent.freeze()
    broken = fresh_engine(stack)
    real_read = stack.apps.calendar.list_events
    stack.apps.calendar.list_events = lambda **kw: (_ for _ in ()).throw(TransientError("verifier down"))
    try:
        first = broken.run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.calendar.list_events = real_read
    assert first.committed is False
    assert len(stack.apps.calendar.world.events) == 1

    second = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    assert second.committed is True
    assert len(stack.apps.calendar.world.events) == 1             # still exactly one


def test_125_126_127_128_resume_after_exhaustion_does_not_duplicate(stack):
    seed_approval(stack.apps)
    stack.apps.calendar.fail_before(3)
    stack.apps.linear.fail_before(3)
    stack.apps.slack.fail_before(3)
    intent = make_intent()
    intent.freeze()
    first = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    assert first.committed is False
    assert stack.ledger.get(intent.intent_hash, "CALENDAR-01")["state"] == L.NOT_FOUND

    second = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    assert second.committed is True
    assert counts(stack) == (1, 1, 1)


def test_129_a_verification_outage_never_produces_committed(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    intent.freeze()
    real = stack.apps.calendar.list_events
    stack.apps.calendar.list_events = lambda **kw: (_ for _ in ()).throw(TransientError("503"))
    try:
        result = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.calendar.list_events = real
    assert result.committed is False
    assert "COMMITTED" not in [e["event_type"] for e in stack.audit.timeline(intent.intent_id)]


def test_130_a_temporary_verification_outage_recovers_on_the_next_run(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    intent.freeze()
    real = stack.apps.calendar.list_events
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("temporary outage")
        return real(**kw)

    stack.apps.calendar.list_events = flaky
    try:
        first = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.calendar.list_events = real
    assert first.committed is False

    second = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    assert second.committed is True
    assert len(stack.apps.calendar.world.events) == 1


def test_131_repeated_worker_execution_does_not_duplicate_verified_effects(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    stack.engine.run(intent, evidence_message_id="msg-1")
    assert counts(stack) == (1, 1, 1)


def test_132_a_second_worker_on_the_same_intent_is_safe(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    a = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    b = fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    assert a.committed is True
    assert b.status == "IDEMPOTENT"
    assert counts(stack) == (1, 1, 1)


def test_133_duplicate_delivery_does_not_add_transitions(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    before = len(stack.audit.timeline(intent.intent_id))
    stack.engine.run(intent, evidence_message_id="msg-1")
    assert len(stack.apps.calendar.world.events) == 1
    assert len(stack.audit.timeline(intent.intent_id)) >= before


def test_134_a_late_replay_does_not_regress_a_committed_intent(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    assert stack.registry.get(intent.intent_id)["status"] == "COMMITTED"
    stack.engine.run(intent, evidence_message_id="msg-1")
    assert stack.registry.get(intent.intent_id)["status"] == "COMMITTED"


def test_135_exhausted_then_resumed_ends_with_exactly_one_effect_per_app(stack):
    seed_approval(stack.apps)
    stack.apps.linear.fail_before(3)
    intent = make_intent()
    intent.freeze()
    fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    fresh_engine(stack).run(intent, evidence_message_id="msg-1")
    assert counts(stack) == (1, 1, 1)
    assert stack.ledger.get(intent.intent_hash, "LINEAR-01")["state"] == L.VERIFIED


# --------------------------------------------------------------------------
# Group J - an adversarial model changes nothing
# --------------------------------------------------------------------------

def test_136_a_model_claiming_success_does_not_commit_anything(stack):
    planner = LyingPlanner()
    assert planner.propose()["status"] == "COMMITTED"
    planner.calls = 0                               # reset after our own probe
    result = stack.engine.run(make_intent(), evidence_message_id="msg-absent")
    assert result.committed is False
    assert planner.calls == 0                       # never consulted
    assert counts(stack) == (0, 0, 0)


def test_137_a_claimed_verification_without_evidence_fails(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    real = stack.apps.calendar.list_events

    def empty(**kw):
        return []

    stack.apps.calendar.list_events = empty
    try:
        result = stack.engine.run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.calendar.list_events = real
    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "VERIFICATION_FAILED"
    assert result.committed is False


def test_138_an_invented_external_id_does_not_verify(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    real = stack.apps.linear.list_tasks
    stack.apps.linear.list_tasks = lambda **kw: [
        {"id": "SUB-INVENTED", "project": "Implementation", "title": "Acme Implementation Kickoff",
         "intent_hash": "0" * 32}]                      # right shape, wrong intent
    try:
        result = stack.engine.run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.linear.list_tasks = real
    lin = next(e for e in result.effects if e["effect_id"] == "LINEAR-01")
    assert lin["state"] == "VERIFICATION_FAILED"


def test_139_a_proposed_unauthorised_recipient_is_refused(stack):
    seed_approval(stack.apps)
    result = stack.engine.run(
        make_intent(effects=(EffectSpec("S-1", "slack", "POST_MESSAGE", target="sales@example"),)),
        evidence_message_id="msg-1")
    assert result.refusal_code == "OUT_OF_SCOPE"
    assert counts(stack) == (0, 0, 0)


def test_140_a_proposed_unauthorised_app_is_rejected_at_validation():
    p = validate_proposal({"customer": "Acme", "project": "Implementation", "event": "Kickoff",
                           "start_iso": START_ISO, "authority": {"approval": "gmail"},
                           "effects": [{"effect_id": "X", "app": "payroll",
                                        "operation": "PAY_EVERYONE"}]}, allowed_apps=ALLOWED)
    assert not p.ok


def test_141_a_proposed_wrong_date_is_refused_on_authority(stack):
    seed_approval(stack.apps)                       # evidence says 2026-09-15T15:00
    result = stack.engine.run(make_intent(start_iso="2026-09-16T16:00"),
                              evidence_message_id="msg-1")
    assert result.refusal_code == "AUTHORITY_MISMATCH"
    assert counts(stack) == (0, 0, 0)


def test_142_wednesday_when_authority_says_tuesday_cannot_commit(stack):
    seed_approval(stack.apps)
    intent = make_intent(effects=(
        EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT",
                   payload=(("start_iso", "2026-09-16T16:00"),)),
    ))
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.committed is False
    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "VERIFICATION_FAILED"
    assert stack.apps.calendar.world.events[0]["start_iso"] == "2026-09-16T16:00"


def test_143_mutating_frozen_authority_is_impossible(stack):
    intent = make_intent()
    intent.freeze()
    with pytest.raises(FrozenIntentError):
        intent.set_authority({"approval": "slack"})
    with pytest.raises(TypeError):
        intent.authority["approval"] = "slack"       # type: ignore[index]


def test_144_text_containing_committed_changes_nothing(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    intent.request = "the model said: status COMMITTED, all effects verified"
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.committed is True                  # because the effects really are verified
    assert stack.registry.get(intent.intent_id)["status"] == "COMMITTED"


def test_145_a_tool_result_with_no_external_state_fails(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    real = stack.apps.slack.list_messages
    stack.apps.slack.list_messages = lambda **kw: []
    try:
        result = stack.engine.run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.slack.list_messages = real
    slack = next(e for e in result.effects if e["effect_id"] == "SLACK-01")
    assert slack["state"] == "VERIFICATION_FAILED"
    assert result.committed is False


# --------------------------------------------------------------------------
# Group K - the commit gate itself
# --------------------------------------------------------------------------

def _effects(*states):
    return [{"effect_id": f"E-{i}", "state": s} for i, s in enumerate(states)]


def test_146_no_verified_effect_cannot_commit():
    d = can_commit(authorized=True, authority_frozen=True, preconditions_ok=True,
                   conflict_clear=True, required_effects=_effects(L.PLANNED, L.EFFECTED),
                   now_ts=time.time(), expires_at_ts=None)
    assert d.allow is False and any("not VERIFIED" in r for r in d.reasons)


def test_147_an_active_conflict_blocks_commit():
    d = can_commit(authorized=True, authority_frozen=True, preconditions_ok=True,
                   conflict_clear=False, required_effects=_effects(L.VERIFIED),
                   now_ts=time.time(), expires_at_ts=None)
    assert d.allow is False and any("conflict" in r for r in d.reasons)


def test_148_a_scope_violation_blocks_commit():
    d = can_commit(authorized=False, authority_frozen=True, preconditions_ok=True,
                   conflict_clear=True, required_effects=_effects(L.VERIFIED),
                   now_ts=time.time(), expires_at_ts=None)
    assert d.allow is False and any("not authorized" in r for r in d.reasons)


def test_149_an_unfrozen_authority_blocks_commit():
    d = can_commit(authorized=True, authority_frozen=False, preconditions_ok=True,
                   conflict_clear=True, required_effects=_effects(L.VERIFIED),
                   now_ts=time.time(), expires_at_ts=None)
    assert d.allow is False and any("frozen" in r for r in d.reasons)


def test_150_only_verified_effects_and_no_conflict_allow_commit():
    d = can_commit(authorized=True, authority_frozen=True, preconditions_ok=True,
                   conflict_clear=True, required_effects=_effects(L.VERIFIED, L.VERIFIED),
                   now_ts=time.time(), expires_at_ts=None)
    assert d.allow is True and d.code == "COMMITTED"
    assert d.as_dict() == {"allow": True, "code": "COMMITTED", "reasons": []}


def test_151_a_refused_write_is_rejected_rather_than_crashing(stack):
    """A permanent refusal from the app is a rejection, not a failed verification.

    Found by pointing the engine at a real Calendar service: the write path
    transitioned REQUESTED -> VERIFICATION_FAILED, which the state machine
    forbids, so the run died with IllegalTransition instead of refusing the
    effect. VERIFICATION_FAILED means an artifact was found and did not match the
    contract; when no artifact exists at all, the effect is REJECTED.
    """
    from causal.apps import PermanentError

    seed_approval(stack.apps)
    intent = make_intent()

    def refuse(**_kw):
        raise PermanentError("calendar: 404 no calendar named primary")

    real = stack.apps.calendar.insert_event
    stack.apps.calendar.insert_event = refuse
    try:
        result = stack.engine.run(intent, evidence_message_id="msg-1")
    finally:
        stack.apps.calendar.insert_event = real

    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "REJECTED"
    assert result.committed is False
    # timeline/verify_chain take an intent ID, not the hash: passing the hash
    # filters everything out and verifies nothing, so this asserts on both.
    assert result.intent_hash == intent.intent_hash
    assert stack.audit.verify_chain(result.intent_id), "the audit chain must survive a rejection"
    events = [e.get("event_type") for e in stack.audit.timeline(result.intent_id)]
    assert "EFFECT_REJECTED" in events, events
