"""Group O - the sign-off boundary and the surface a non-engineer reads.

Two claims, both checkable:

  1. An effect that reaches the outside world does not happen until a named human
     approves it, and the internal effects in the same intent are not held up by it.
  2. Everything the review page says is a state the ledger actually holds. The plain
     English is a translation, never a second opinion, and it never invents a claim
     the system cannot support.

The boundary is the operator's to declare. Empty by default, so nothing in the shipped
system silently starts waiting for a person because this file added a feature.
"""
from __future__ import annotations
import json
import pytest
from fastapi.testclient import TestClient
from causal import plain
from causal import scenarios as S
from causal.engine import Causal
from causal.ledger import AWAITING_APPROVAL, VERIFIED
WED = "2026-10-07T15:00"
def _stack(tmp_path, name="review.db"):
    return S.fresh_stack("LOCAL", str(tmp_path / name))
def _signoff_run(stack, intent_id="O-1", *, approver: str | None = None):
    """One outbound intent, run with Slack declared as reaching the outside world."""
    stack.engine.signoff_apps = {"slack"}
    msg = S.seed_approval(stack, "Margie", "renewal", WED, f"msg-{intent_id}")
    intent = S.build_binding_intent("Margie", "renewal", WED, intent_id=intent_id)
    result = stack.engine.run(intent, evidence_message_id=msg)
    stack.engine.signoff_apps = None
    if approver:
        for effect in stack.ledger.all(intent.intent_hash):
            if effect["state"] == AWAITING_APPROVAL:
                stack.ledger.record_approval(intent_hash=intent.intent_hash,
                                             effect_id=effect["effect_id"],
                                             approved_by=approver, reason="reviewed")
    return intent, result


# -- the boundary ---------------------------------------------------------

def test_outbound_effect_waits_and_internal_effects_do_not(tmp_path):
    stack = _stack(tmp_path)
    intent, result = _signoff_run(stack)

    states = {e["effect_id"]: e["state"] for e in stack.ledger.all(intent.intent_hash)}
    assert states["SLACK-01"] == AWAITING_APPROVAL
    assert states["CALENDAR-01"] == VERIFIED
    assert states["LINEAR-01"] == VERIFIED
    assert result.committed is False
def test_the_outbound_write_never_happens_before_approval(tmp_path):
    stack = _stack(tmp_path)
    _, result = _signoff_run(stack)

    # The claim is about the world, not about a flag: nothing was posted.
    assert stack.apps.slack.world.messages == []
    assert len(stack.apps.calendar.world.events) == 1
    assert len(stack.apps.linear.world.tasks) == 1
    assert result.status != "COMMITTED"
def test_the_refusal_names_the_signoff_not_a_generic_failure(tmp_path):
    stack = _stack(tmp_path)
    _, result = _signoff_run(stack)
    assert result.refusal_code == "AWAITING_APPROVAL"
def test_approval_by_a_named_human_completes_the_job(tmp_path):
    stack = _stack(tmp_path)
    intent, _ = _signoff_run(stack, approver="dana.reyes@acme.example")

    msg = f"msg-{intent.intent_id}"
    second = stack.engine.run(intent, evidence_message_id=msg)
    assert second.committed is True
    assert len(stack.apps.slack.world.messages) == 1
    # exactly once, not twice
    assert len(stack.apps.calendar.world.events) == 1
def test_the_approval_is_stored_with_who_gave_it(tmp_path):
    stack = _stack(tmp_path)
    intent, _ = _signoff_run(stack, approver="dana.reyes@acme.example")
    approvals = stack.ledger.approvals(intent.intent_hash)
    assert [a["approved_by"] for a in approvals] == ["dana.reyes@acme.example"]
    assert approvals[0]["created_at"] > 0
def test_the_approval_is_audited_both_ways(tmp_path):
    stack = _stack(tmp_path)
    intent, _ = _signoff_run(stack, approver="dana.reyes@acme.example")
    stack.engine.run(intent, evidence_message_id=f"msg-{intent.intent_id}")
    events = json.dumps(stack.audit.timeline(intent.intent_id))
    assert "APPROVAL_REQUIRED" in events
    assert "APPROVAL_GRANTED" in events
def test_an_operator_policy_does_not_leak_into_the_next_run(tmp_path):
    """The declaration is per-run. A boundary nobody asked for is a surprise."""
    stack = _stack(tmp_path, "leak.db")
    S.run("awaiting_signoff", stack)
    assert stack.engine.signoff_apps is None
    S.run("intended", stack)
    assert stack.engine.signoff_apps is None
def test_nothing_waits_for_a_person_unless_the_operator_says_so(tmp_path):
    stack = _stack(tmp_path, "quiet.db")
    msg = S.seed_approval(stack, "Dana", "rollout", WED, "msg-quiet")
    intent = S.build_binding_intent("Dana", "rollout", WED, intent_id="O-2")
    result = stack.engine.run(intent, evidence_message_id=msg)
    assert result.committed is True
    assert not stack.ledger.approvals(intent.intent_hash)


# -- the words ------------------------------------------------------------

def test_every_ledger_state_has_a_phrase():
    from causal import ledger
    states = {"PLANNED", "REQUESTED", "EFFECTED", "VERIFYING", "VERIFIED", "UNKNOWN",
              "RECONCILING", "NOT_FOUND", "NOT_PROVABLE", "AMBIGUOUS", AWAITING_APPROVAL,
              "POST_COMMIT_DUPLICATE", "VERIFICATION_FAILED", "REJECTED", "ESCALATED"}
    missing = states - set(plain.EFFECT)
    assert not missing, f"a state a person can see has no phrase: {sorted(missing)}"
