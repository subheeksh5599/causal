"""Group A - Intent contract (001-015) and Group B - Authority freeze (016-030).

Each test is one claim. Nothing here asserts against a canned expected
return value; every claim is checked against a real object or a real database.
"""

from __future__ import annotations

import re

import pytest

from causal import Intent, Scope, conflict_key_for, validate_proposal
from causal.audit import AUTHORITY_FROZEN
from causal.intent import EffectSpec
from causal.intent import FrozenIntentError
from causal.registry import CONFLICT, REGISTERED

from .conftest import CONTACT, make_intent, seed_approval

ALLOWED = {"gmail", "calendar", "linear", "slack"}

BASE = {
    "customer": "Acme",
    "project": "Implementation",
    "event": "Kickoff",
    "start_iso": "2026-09-15T15:00",
    "timezone": "Europe/London",
    "authority": {"approval": "gmail", "time": "gmail"},
    "effects": [{"effect_id": "CALENDAR-01", "app": "calendar", "operation": "CREATE_EVENT"}],
}


def prop(**over):
    return validate_proposal({**BASE, **over}, allowed_apps=ALLOWED)


# --------------------------------------------------------------------------
# Group A - intent contract
# --------------------------------------------------------------------------

def test_001_valid_request_produces_a_valid_contract():
    p = prop()
    assert p.ok and p.scope.customer == "Acme" and len(p.effects) == 1


@pytest.mark.parametrize("field", ["event", "customer", "project", "start_iso"])
def test_002_to_004_missing_required_field_is_rejected(field):
    p = prop(**{field: None})
    assert not p.ok
    assert any(f"missing required field {field!r}" == r for r in p.reasons)


def test_005_empty_effect_list_is_rejected():
    p = prop(effects=[])
    assert not p.ok and any("non-empty list" in r for r in p.reasons)


def test_006_unsupported_app_is_rejected():
    p = prop(effects=[{"effect_id": "X", "app": "salesforce", "operation": "UPDATE"}])
    assert not p.ok and any("unsupported app" in r for r in p.reasons)


def test_007_malformed_date_is_rejected():
    p = prop(start_iso="15-09-2026")
    assert not p.ok and any("malformed start_iso" in r for r in p.reasons)


def test_008_malformed_timezone_is_rejected():
    p = prop(timezone="GMT+1")
    assert not p.ok and any("malformed timezone" in r for r in p.reasons)


def test_009_empty_authority_map_is_rejected():
    p = prop(authority={})
    assert not p.ok and any("authority map is empty" in r for r in p.reasons)


def test_010_duplicate_effect_ids_are_rejected():
    p = prop(effects=[
        {"effect_id": "DUP", "app": "calendar", "operation": "CREATE_EVENT"},
        {"effect_id": "DUP", "app": "linear", "operation": "CREATE_ISSUE"},
    ])
    assert not p.ok and any("duplicate effect_id" in r for r in p.reasons)


def test_011_canonical_serialization_is_deterministic():
    a = Intent._canonical_json({"b": 1, "a": [1, 2]})
    b = Intent._canonical_json({"a": [1, 2], "b": 1})
    assert a == b and " " not in a


def test_012_same_semantic_intent_produces_the_same_hash():
    assert make_intent().freeze() == make_intent().freeze()


def test_013_semantically_different_intent_produces_a_different_hash():
    assert make_intent().freeze() != make_intent(project="Migration").freeze()


def test_014_effect_order_and_recipient_order_do_not_change_the_hash():
    a = make_intent(recipients=("#engineering", "#ops"))
    b = make_intent(recipients=("#ops", "#engineering"))
    a.effects, b.effects = tuple(reversed(a.effects)), b.effects
    assert a.freeze() == b.freeze()


@pytest.mark.parametrize("kwargs", [
    {"start_iso": "2026-09-15T16:00"},
    {"customer": "Contoso"},
    {"project": "Migration"},
    {"event": "Retro"},
])
def test_015_material_field_changes_change_the_hash(kwargs):
    assert make_intent().freeze() != make_intent(**kwargs).freeze()


# --------------------------------------------------------------------------
# Group B - authority freeze
# --------------------------------------------------------------------------

def test_016_valid_authority_snapshot_can_be_created():
    intent = make_intent()
    intent.freeze()
    assert re.fullmatch(r"[0-9a-f]{32}", intent.authority_hash) and intent.status == "AUTHORIZED"


def test_017_authority_hash_is_deterministic():
    assert make_intent().freeze() == make_intent().freeze()


def test_018_authority_cannot_be_frozen_twice_with_a_different_result():
    intent = make_intent()
    first = intent.freeze()
    second = intent.freeze()
    assert first == second


def test_019_frozen_authority_cannot_be_mutated_through_the_api():
    intent = make_intent()
    intent.freeze()
    with pytest.raises(FrozenIntentError):
        intent.set_authority({"approval": "slack"})


def test_020_frozen_authority_cannot_be_mutated_by_model_output():
    intent = make_intent()
    intent.freeze()
    with pytest.raises(FrozenIntentError):
        intent.guard_mutation("authority")


def test_021_frozen_authority_cannot_be_mutated_in_place():
    """A mapping proxy, so even a reference-holding caller cannot rewrite it."""
    intent = make_intent()
    intent.freeze()
    with pytest.raises(TypeError):
        intent.authority["approval"] = "slack"       # type: ignore[index]


def test_022_repeated_planning_call_does_not_change_the_frozen_hash():
    intent = make_intent()
    h = intent.freeze()
    for _ in range(3):
        intent.freeze()
    assert intent.intent_hash == h and intent.authority_hash is not None


@pytest.mark.parametrize("fact", ["approval", "time", "meeting", "work", "notification"])
def test_023_to_025_changing_any_authority_fact_after_freeze_is_rejected(fact):
    intent = make_intent()
    intent.freeze()
    with pytest.raises(FrozenIntentError):
        intent.set_authority({fact: "slack"})


def test_026_authority_hash_changes_when_the_authority_definition_changes():
    a = make_intent(authority={"approval": "gmail"})
    b = make_intent(authority={"approval": "slack"})
    a.freeze()
    b.freeze()
    assert a.authority_hash != b.authority_hash


def test_027_historical_authority_snapshot_remains_readable_after_completion(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    frozen = [e for e in stack.audit.timeline(intent.intent_id) if e["event_type"] == AUTHORITY_FROZEN]
    assert frozen
    assert "approval" in frozen[0]["metadata"]["authority"]
    assert frozen[0]["metadata"]["authority_hash"]


def test_028_verification_uses_the_frozen_authority_not_current_configuration(stack):
    """The engine's authority view is the intent's own, and the refusal cites both values."""
    seed_approval(stack.apps, body="Acme approved the implementation. Kickoff 2026-09-15T16:00.")
    intent = make_intent(start_iso="2026-09-15T15:00")
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.refusal_code == "AUTHORITY_MISMATCH"
    assert any("2026-09-15T16:00" in r and "2026-09-15T15:00" in r for r in result.reasons)


def test_029_a_second_agent_cannot_overwrite_the_first_authority_snapshot(stack):
    seed_approval(stack.apps)
    a = make_intent(intent_id="C-A")
    a.freeze()
    stack.registry.register(intent_id=a.intent_id, conflict_key=a.conflict_key, intent_hash=a.intent_hash)
    b = make_intent(intent_id="C-B", start_iso="2026-09-15T16:00")
    b.freeze()
    outcome = stack.registry.register(intent_id=b.intent_id, conflict_key=b.conflict_key,
                                      intent_hash=b.intent_hash)
    assert outcome["outcome"] == CONFLICT
    assert stack.registry.get(a.intent_id)["intent_hash"] == a.intent_hash
    assert stack.registry.get(a.intent_id)["status"] == "ACTIVE"


def test_030_authority_snapshot_appears_in_the_audit_log_with_hash_and_timestamp(stack):
    seed_approval(stack.apps)
    intent = make_intent()
    stack.engine.run(intent, evidence_message_id="msg-1")
    row = next(e for e in stack.audit.timeline(intent.intent_id) if e["event_type"] == AUTHORITY_FROZEN)
    assert row["created_at"] > 0
    assert row["prev_hash"] != "" and row["event_hash"] != ""
    assert stack.audit.verify_chain(intent.intent_id)
