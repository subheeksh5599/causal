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
