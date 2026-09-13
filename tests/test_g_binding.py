"""Group M - the binding layer, measured rather than asserted.

The product claim is not "we find something similar". It is "we can prove which
external effect belongs to this intent, and we refuse when we cannot". So this file
computes the four rates that claim lives or dies by:

    semantic recovery rate   of crash-after-write cases, how many bind the existing
                             effect instead of creating a second one
    false binding rate       how often an unrelated but similar object gets attributed
                             to an intent. This must be zero, because the consequence
                             of a false bind is a COMMITTED intent whose effect never
                             happened.
    duplicate prevention     two workers, one real-world effect
    ambiguity refusal rate   two equivalent candidates, does it refuse or guess

Plus the policy mechanics: matchability per surface, the authored exclusivity
relation, leases, and the post-commit duplicate scan.
"""
from __future__ import annotations
import pytest
from causal import binding
from causal import ledger as L
from causal.scenarios import (ACME_PEOPLE, REFERENCE_DATE, TUESDAY, build_binding_intent,
                              fresh_stack, run, seed_approval)
def _plus_days(iso: str, days: int) -> str:
    """A near-miss date relative to the fixture clock, so this file holds no literal."""
    from datetime import datetime, timedelta
    return (datetime.fromisoformat(iso) + timedelta(days=days)).isoformat(timespec="minutes")
CASES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
def _crash_stack(tmp_path, name):
    stack = fresh_stack("LOCAL", str(tmp_path / f"{name}.db"))
    msg = seed_approval(stack, name, "cutover", TUESDAY, f"msg-{name}")
    intent = build_binding_intent(name, "Cutover", TUESDAY, intent_id=f"C-{name}")
    return stack, msg, intent, f"{name} Cutover Kickoff"


# ---------------------------------------------------------------- the four rates

def test_semantic_recovery_rate_is_complete_over_the_seeded_cases(tmp_path):
    """Of crash-after-write cases where the effect exists but carries no tag of ours,
    how many does the system bind rather than duplicate? Seeded suite, so the number
    is a result rather than a claim."""
    recovered = 0
    details = []
    for i, name in enumerate(CASES):
        stack, msg, intent, title = _crash_stack(tmp_path, name)
        try:
            a_effect = stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                                        attendees=intent.scope.recipients)
            stack.apps.calendar.fail_before(1)      # our write never lands
            result = stack.engine.run(intent, evidence_message_id=msg)
            found = stack.apps.calendar.search_candidates(start_iso=TUESDAY, title=title)
            cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
            bound = (cal["state"] == L.VERIFIED and cal.get("external_id") == a_effect["id"]
                     and len(found) == 1)
            recovered += 1 if bound else 0
            details.append({"case": name, "state": cal["state"],
                            "external_id": cal.get("external_id"),
                            "a_effect_id": a_effect["id"], "artifacts": len(found)})
        finally:
            stack.close()
    rate = recovered / len(CASES)
    assert rate == 1.0, f"semantic recovery rate {rate}: {details}"
def test_false_binding_rate_is_zero_on_near_misses(tmp_path):
    """A similar but unrelated object must never be bound to this intent.

    The near-miss below is the dangerous shape: same customer, same project, same
    participants, same title — a different day. A looser fingerprint would attribute
    it, skip the write, and commit an intent whose effect never happened.
    """
    false_binds = 0
    checked = 0
    for name in CASES:
        stack, msg, intent, title = _crash_stack(tmp_path, name)
        try:
            # a genuinely separate meeting, created by a human
            near_miss = stack.apps.calendar.foreign_event(
                title=title, start_iso=_plus_days(TUESDAY, 7), attendees=intent.scope.recipients)
            stack.apps.calendar.fail_before(1)
            result = stack.engine.run(intent, evidence_message_id=msg)
            cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
            checked += 1
            if cal.get("external_id") == near_miss["id"]:
                false_binds += 1
            # and the separate meeting is still separate
            assert len(stack.apps.calendar.list_events(intent_hash=intent.intent_hash)) == 1
        finally:
            stack.close()
    assert checked == len(CASES)
    assert false_binds == 0, f"false binding rate {false_binds / checked}"
def test_duplicate_prevention_rate(tmp_path):
    """Two workers, one real-world effect, every time."""
    ok = 0
    for name in CASES:
        stack, msg, intent, title = _crash_stack(tmp_path, name)
        try:
            a_effect = stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                                        attendees=intent.scope.recipients)
            stack.registry.register(intent_id=intent.intent_id,
                                    conflict_key=intent.conflict_key,
                                    intent_hash=(lambda i: (i.freeze(), i.intent_hash)[1])(
                                        build_binding_intent(name, "Cutover", TUESDAY,
                                                             intent_id="probe")))
            stack.registry.expire_lease(intent.intent_id)
            stack.apps.calendar.fail_before(1)
            result = stack.engine.run(intent, evidence_message_id=msg)
            artifacts = stack.apps.calendar.search_candidates(start_iso=TUESDAY, title=title)
            if result.committed and len(artifacts) == 1 and artifacts[0]["id"] == a_effect["id"]:
                ok += 1
        finally:
            stack.close()
    assert ok == len(CASES), f"duplicate prevention {ok}/{len(CASES)}"
