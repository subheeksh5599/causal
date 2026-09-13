"""Groups C (evidence), D (scope/policy), E (conflict) - tests 031-075."""

from __future__ import annotations

import time

import pytest

from causal import EffectSpec, validate_proposal
from causal import ledger as L
from causal.audit import CONFLICT_DETECTED, EVIDENCE_ACCEPTED
from causal.policy import can_execute
from causal.registry import CONFLICT, IDEMPOTENT, REGISTERED, RESUME, SUPERSEDE, Registry

from .conftest import CONTACT, START_ISO, make_intent, seed_approval

ALLOWED = {"gmail", "calendar", "linear", "slack"}


def writes(stack):
    return (len(stack.apps.calendar.world.events), len(stack.apps.linear.world.tasks),
            len(stack.apps.slack.world.messages))


# --------------------------------------------------------------------------
# Group C - evidence gate
# --------------------------------------------------------------------------

def test_031_valid_approval_satisfies_the_gate(stack):
    seed_approval(stack.apps)
    assert stack.engine.run(make_intent(), evidence_message_id="msg-1").committed is True


@pytest.mark.parametrize("body,sender,expect", [
    (None, CONTACT, "EVIDENCE_MISSING"),                                       # 032 no message at all
    ("Acme approved the implementation.", "stranger@evil.example", "EVIDENCE_MISSING"),   # 033
    ("Contoso approved the implementation. 2026-09-15T15:00.", CONTACT, "EVIDENCE_MISSING"),  # 034
    ("Acme approved the migration. 2026-09-15T15:00.", CONTACT, "EVIDENCE_MISSING"),      # 035
    ("Acme Implementation Kickoff 2026-09-15T15:00.", CONTACT, "EVIDENCE_MISSING"),        # 036
    ("", CONTACT, "EVIDENCE_MISSING"),                                                     # 039
])
def test_032_to_039_evidence_gate_refuses(stack, body, sender, expect):
    if body is None:
        result = stack.engine.run(make_intent(), evidence_message_id="msg-absent")
    else:
        seed_approval(stack.apps, body=body, sender=sender)
        result = stack.engine.run(make_intent(), evidence_message_id="msg-1")
    assert result.refusal_code == expect
    assert writes(stack) == (0, 0, 0)


def test_037_stale_approval_is_refused(stack):
    seed_approval(stack.apps, timestamp=time.time() - 10 * 86400)
    result = stack.engine.run(make_intent(approval_max_age_s=86400), evidence_message_id="msg-1")
    assert result.refusal_code == "EVIDENCE_STALE"
    assert writes(stack) == (0, 0, 0)


def test_038_future_dated_approval_is_refused(stack):
    seed_approval(stack.apps, timestamp=time.time() + 3600)
    result = stack.engine.run(make_intent(), evidence_message_id="msg-1")
    assert result.refusal_code == "EVIDENCE_MISSING"
    assert writes(stack) == (0, 0, 0)


def test_040_deleted_source_artifact_is_refused(stack):
    result = stack.engine.run(make_intent(), evidence_message_id="msg-does-not-exist")
    assert result.refusal_code == "EVIDENCE_MISSING"
    assert writes(stack) == (0, 0, 0)


def test_041_changed_source_artifact_is_caught_by_the_re_read(stack):
    """Evidence is read at run time, not cached at intent creation."""
    seed_approval(stack.apps, body="Acme approved the implementation. Kickoff 2026-09-15T15:00.")
    intent = make_intent(start_iso="2026-09-15T15:00")
    stack.apps.mail.seed_message("msg-1", sender=CONTACT,
                                 body="Acme approved the implementation. Kickoff 2026-09-15T19:00.")
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.refusal_code == "AUTHORITY_MISMATCH"
    assert writes(stack) == (0, 0, 0)


def test_042_043_044_evidence_is_persisted_with_hash_source_and_audit(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    row = next(e for e in stack.audit.timeline(intent.intent_id)
               if e["event_type"] == EVIDENCE_ACCEPTED)
    assert row["metadata"]["content_hash"]
    assert row["metadata"]["message_id"] == "msg-1"
    assert row["metadata"]["source_app"] == "gmail"


def test_045_evidence_failure_produces_no_external_write(stack):
    result = stack.engine.run(make_intent(), evidence_message_id="msg-absent")
    assert result.refusal_code == "EVIDENCE_MISSING"
    assert writes(stack) == (0, 0, 0)
    assert result.audit[-1]["event_type"] == "INTENT_REFUSED"


# --------------------------------------------------------------------------
# Group D - scope and policy
# --------------------------------------------------------------------------

def test_046_a_valid_effect_inside_scope_is_allowed(stack):
    intent = make_intent()
    intent.freeze()
    spec = EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT")
    d = can_execute(intent, spec, effect_state=L.PLANNED, now_ts=time.time(),
                    authority_frozen=True, conflict_clear=True)
    assert d.allow


@pytest.mark.parametrize("effect,reason", [
    ({"effect_id": "X", "app": "salesforce", "operation": "UPDATE"}, "unsupported app"),     # 047
    ({"effect_id": "X", "app": "calendar", "operation": "DELETE_ALL"}, "unsupported operation"),  # 048
])
def test_047_048_unknown_app_or_operation_is_rejected(effect, reason):
    p = validate_proposal({"customer": "Acme", "project": "Implementation", "event": "Kickoff",
                           "start_iso": START_ISO, "authority": {"approval": "gmail"},
                           "effects": [effect]}, allowed_apps=ALLOWED)
    assert not p.ok and any(reason in r for r in p.reasons)


def test_049_050_scope_rejects_the_wrong_subject():
    scope = make_intent().scope
    assert not scope.allows(customer="Contoso", project="Implementation", event="Kickoff")
    assert not scope.allows(customer="Acme", project="Migration", event="Kickoff")
    assert scope.allows(customer="Acme", project="Implementation", event="Kickoff")


@pytest.mark.parametrize("scope_time,evidence_time", [
    ("2026-09-15T15:00", "2026-09-15T16:00"),   # 052 wrong date/time vs authority
    ("2026-09-15T16:00", "2026-09-15T15:00"),   # 053 the other direction
])
def test_052_053_authority_mismatch_refuses_before_any_write(stack, scope_time, evidence_time):
    seed_approval(stack.apps, body=f"Acme approved the implementation. Kickoff {evidence_time}.")
    result = stack.engine.run(make_intent(start_iso=scope_time), evidence_message_id="msg-1")
    assert result.refusal_code == "AUTHORITY_MISMATCH"
    assert writes(stack) == (0, 0, 0)


def test_054_malformed_timezone_is_rejected():
    p = validate_proposal({"customer": "Acme", "project": "Implementation", "event": "Kickoff",
                           "start_iso": START_ISO, "timezone": "GMT+1",
                           "authority": {"approval": "gmail"},
                           "effects": [{"effect_id": "X", "app": "calendar",
                                        "operation": "CREATE_EVENT"}]}, allowed_apps=ALLOWED)
    assert not p.ok and any("malformed timezone" in r for r in p.reasons)


@pytest.mark.parametrize("app,target", [
    ("slack", "#general"),                       # 057 unauthorised channel
    ("slack", "sales@example"),                  # 051 unauthorised recipient
    ("linear", "team-other"),                    # 058 unauthorised team
    ("calendar", "intruder@example.com"),        # 059 unauthorised attendee
])
def test_051_057_058_059_targets_outside_the_scope_are_refused(stack, app, target):
    seed_approval(stack.apps)
    op = {"slack": "POST_MESSAGE", "linear": "CREATE_ISSUE", "calendar": "CREATE_EVENT"}[app]
    intent = make_intent(effects=(EffectSpec("E-1", app, op, target=target),))
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.refusal_code == "OUT_OF_SCOPE"
    assert writes(stack) == (0, 0, 0)


def test_055_an_extra_unauthorised_effect_refuses_the_whole_intent(stack):
    seed_approval(stack.apps)
    intent = make_intent(effects=(
        EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT"),
        EffectSpec("SLACK-99", "slack", "POST_MESSAGE", target="#random"),
    ))
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.refusal_code == "OUT_OF_SCOPE"
    assert writes(stack) == (0, 0, 0)          # the calendar effect did not run either


def test_056_an_unplanned_effect_cannot_be_written(stack):
    assert stack.ledger.may_write("deadbeef", "NEVER-PLANNED") is False


def test_060_scope_violation_causes_zero_writes_everywhere(stack):
    seed_approval(stack.apps)
    result = stack.engine.run(
        make_intent(effects=(EffectSpec("E-1", "slack", "POST_MESSAGE", target="#leak"),)),
        evidence_message_id="msg-1")
    assert result.refusal_code == "OUT_OF_SCOPE"
    assert writes(stack) == (0, 0, 0)


# --------------------------------------------------------------------------
# Group E - conflict
# --------------------------------------------------------------------------

def _reg(registry, iid, key, h):
    return registry.register(intent_id=iid, conflict_key=key, intent_hash=h)


def test_061_no_existing_intent_allows_the_key(stack):
    assert _reg(stack.registry, "A", "ACME::IMP::KICK", "h1")["outcome"] == REGISTERED


def test_062_exact_duplicate_key_with_a_different_hash_conflicts(stack):
    _reg(stack.registry, "A", "ACME::IMP::KICK", "h1")
    assert _reg(stack.registry, "B", "ACME::IMP::KICK", "h2")["outcome"] == CONFLICT


def test_063_identical_hash_is_idempotent_not_a_conflict(stack):
    _reg(stack.registry, "A", "ACME::IMP::KICK", "h1")
    assert _reg(stack.registry, "B", "ACME::IMP::KICK", "h1")["outcome"] == IDEMPOTENT


def test_064_two_identical_intents_do_not_double_act(stack):
    seed_approval(stack.apps)
    first = stack.engine.run(make_intent(intent_id="C-A"), evidence_message_id="msg-1")
    second = stack.engine.run(make_intent(intent_id="C-B"), evidence_message_id="msg-1")
    assert first.committed is True
    assert second.status == "IDEMPOTENT"
    assert len(stack.apps.calendar.world.events) == 1
    assert len(stack.apps.linear.world.tasks) == 1


def test_065_second_submission_is_refused_while_the_first_is_active(stack):
    seed_approval(stack.apps)
    seed_approval(stack.apps, body="Acme approved the implementation. Kickoff 2026-09-15T16:00.",
                  message_id="msg-2")
    a = make_intent(intent_id="C-A")
    a.freeze()
    _reg(stack.registry, a.intent_id, a.conflict_key, a.intent_hash)
    result = stack.engine.run(make_intent(intent_id="C-B", start_iso="2026-09-15T16:00"),
                              evidence_message_id="msg-2")
    assert result.refusal_code == "CONFLICT"
    assert writes(stack) == (0, 0, 0)


def test_066_a_completed_outcome_supersedes_instead_of_conflicting(stack):
    seed_approval(stack.apps)
    stack.engine.run(make_intent(intent_id="C-A"), evidence_message_id="msg-1")
    out = _reg(stack.registry, "C-B", "ACME::IMPLEMENTATION::KICKOFF", "different-hash")
    assert out["outcome"] == SUPERSEDE


@pytest.mark.parametrize("field,value", [
    ("customer", "Contoso"),     # 067
    ("project", "Migration"),    # 068
    ("event", "Retro"),          # 069
])
def test_067_to_069_different_business_outcomes_do_not_conflict(stack, field, value):
    base = make_intent()
    other = make_intent(**{field: value})
    assert base.conflict_key != other.conflict_key
    assert _reg(stack.registry, "A", base.conflict_key, "h1")["outcome"] == REGISTERED
    assert _reg(stack.registry, "B", other.conflict_key, "h2")["outcome"] == REGISTERED


def test_070_same_customer_different_operation_does_not_conflict(stack):
    a = make_intent()
    b = make_intent(event="Handover")
    assert a.conflict_key != b.conflict_key


def test_071_the_same_outcome_at_a_different_time_collides(stack):
    """The whole point of excluding the disputed fact from the key."""
    a = make_intent(start_iso="2026-09-15T15:00")
    b = make_intent(start_iso="2026-09-15T16:00")
    assert a.conflict_key == b.conflict_key
    _reg(stack.registry, "A", a.conflict_key, "h-a")
    assert _reg(stack.registry, "B", b.conflict_key, "h-b")["outcome"] == CONFLICT


def test_072_same_time_different_outcome_does_not_conflict(stack):
    a = make_intent(event="Kickoff")
    b = make_intent(event="Retro")
    assert a.conflict_key != b.conflict_key


def test_073_conflict_survives_a_process_restart(stack):
    _reg(stack.registry, "A", "ACME::IMP::KICK", "h1")
    reopened = Registry(stack.path)
    try:
        assert reopened.active_for("ACME::IMP::KICK")["intent_id"] == "A"
    finally:
        reopened.close()


def test_074_the_losing_intent_is_persisted_as_blocked(stack):
    _reg(stack.registry, "A", "ACME::IMP::KICK", "h1")
    _reg(stack.registry, "B", "ACME::IMP::KICK", "h2")
    assert stack.registry.get("B")["status"] == "BLOCKED"


def test_075_conflict_is_recorded_in_the_audit_history(stack):
    seed_approval(stack.apps)
    seed_approval(stack.apps, body="Acme approved the implementation. Kickoff 2026-09-15T16:00.",
                  message_id="msg-2")
    a = make_intent(intent_id="C-A")
    a.freeze()
    _reg(stack.registry, a.intent_id, a.conflict_key, a.intent_hash)
    b = make_intent(intent_id="C-B", start_iso="2026-09-15T16:00")
    stack.engine.run(b, evidence_message_id="msg-2")
    assert any(e["event_type"] == CONFLICT_DETECTED for e in stack.audit.timeline("C-B"))
