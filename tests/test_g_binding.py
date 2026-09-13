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


def test_ambiguity_refusal_rate(tmp_path):
    """Two equivalent candidates means ownership cannot be proved, so nothing commits."""
    refusals = 0
    for name in CASES:
        stack, msg, intent, title = _crash_stack(tmp_path, name)
        try:
            stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                              attendees=intent.scope.recipients)
            stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                              attendees=intent.scope.recipients)
            result = stack.engine.run(intent, evidence_message_id=msg)
            cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
            if cal["state"] == L.AMBIGUOUS and not result.committed:
                refusals += 1
        finally:
            stack.close()
    assert refusals == len(CASES), f"ambiguity refusal {refusals}/{len(CASES)}"


def test_foreign_write_is_detected_and_classified(tmp_path):
    """An object this system never wrote is still found, and reported as what it is."""
    stack, msg, intent, title = _crash_stack(tmp_path, "Golf")
    try:
        foreign = stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                                   attendees=intent.scope.recipients)
        verdict = stack.engine._bind(intent, intent.effects[0])
        assert verdict.outcome == binding.ONE_MATCH
        assert verdict.matched["id"] == foreign["id"]
        assert verdict.matched["intent_hash"] == ""      # nothing of ours tagged it
    finally:
        stack.close()


# ------------------------------------------------------- the policy mechanics

def test_fingerprint_is_deterministic_across_independent_construction():
    a = binding.Fingerprint.of(entity="Acme", operation="renew", resource="CONTRACT-482",
                               title="Acme Renewal", start_iso="2026-10-07T15:00",
                               participants=("bob@x.test", "alice@x.test"))
    b = binding.Fingerprint.of(entity="acme", operation="RENEW", resource="contract-482",
                               title="  Acme   Renewal ", start_iso="2026-10-07T15:00:00",
                               participants=("alice@x.test", "bob@x.test"))
    assert a.digest() == b.digest(), "canonicalisation must make worker A and B agree"


def test_canonicalisation_is_authored_not_inferred():
    assert binding.canon("Acme, Inc.") == "acme inc"
    assert binding.canon("  a   b  ") == "a b"
    assert binding.canon(None) == ""
    assert binding.canon("A/B-C") == "a b c"


def test_a_near_miss_does_not_match():
    intended = binding.Fingerprint.of(entity="Acme", operation="renew", title="Acme Renewal",
                                      start_iso="2026-10-07T15:00",
                                      participants=("alice@x.test",))
    other_day = {"title": "Acme Renewal", "start_iso": "2026-10-08T15:00",
                 "participants": ["alice@x.test"]}
    assert intended.matches(other_day) is False


def test_classify_reports_not_provable_only_where_absence_cannot_be_shown():
    intended = binding.Fingerprint.of(entity="Acme", operation="post", title="Acme Renewal")
    provable = binding.classify(surface="calendar", intended=intended, candidates=[])
    assert provable.outcome == binding.NOT_FOUND
    unprovable = binding.classify(surface="gmail", intended=intended, candidates=[])
    assert unprovable.outcome == binding.NOT_PROVABLE
    # and an operator can re-policy a surface
    overridden = binding.classify(surface="slack", intended=intended, candidates=[],
                                  matchability=binding.BEST_EFFORT)
    assert overridden.outcome == binding.NOT_PROVABLE


def test_classify_distinguishes_mismatch_from_absence():
    intended = binding.Fingerprint.of(entity="Acme", operation="renew", title="Acme Renewal",
                                      start_iso="2026-10-07T15:00")
    stranger = [{"title": "Different meeting", "start_iso": "2026-10-07T15:00"}]
    verdict = binding.classify(surface="calendar", intended=intended, candidates=stranger)
    assert verdict.outcome == binding.MISMATCH


def test_exclusivity_relation_is_authored_versioned_and_hashed():
    assert binding.exclusive("renew", "cancel_renew") is True
    assert binding.exclusive("cancel_renew", "RENEW") is True        # order-insensitive
    assert binding.exclusive("renew", "renew") is False
    assert binding.exclusive("renew", "reschedule") is False
    assert len(binding.EXCLUSIVITY_DIGEST) == 32
    assert binding.EXCLUSIVITY_DIGEST == binding.exclusivity_digest()
    assert binding.EXCLUSIVITY_VERSION == "v1"


def test_lease_blocks_a_second_worker_until_it_expires():
    """Two workers on one job: the second must not run while the first holds it, and
    must take it over once that hold is released."""
    from causal.registry import IDEMPOTENT, REGISTERED, RESUME, Registry

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        reg = Registry(f"{d}/r.db")
        try:
            first = reg.register(intent_id="C-1", conflict_key="K", intent_hash="H")
            assert first["outcome"] == REGISTERED
            second = reg.register(intent_id="C-1", conflict_key="K", intent_hash="H")
            assert second["outcome"] == IDEMPOTENT
            reg.expire_lease("C-1")
            third = reg.register(intent_id="C-1", conflict_key="K", intent_hash="H")
            assert third["outcome"] == RESUME
        finally:
            reg.close()


def test_a_not_provable_effect_is_never_retried():
    """The state has no path back to requesting a write."""
    assert L.REQUESTED not in L.LEGAL_TRANSITIONS[L.NOT_PROVABLE]
    assert L.NOT_PROVABLE in L.NO_RETRY_STATES
    assert L.REQUESTED not in L.LEGAL_TRANSITIONS[L.POST_COMMIT_DUPLICATE]


def test_takeover_binds_before_writing(tmp_path):
    stack, msg, intent, title = _crash_stack(tmp_path, "Hotel")
    try:
        stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                          attendees=intent.scope.recipients)
        a = build_binding_intent("Hotel", "Cutover", TUESDAY, intent_id="C-Hotel")
        a.freeze()
        stack.registry.register(intent_id=a.intent_id, conflict_key=a.conflict_key,
                                intent_hash=a.intent_hash)
        stack.registry.expire_lease(a.intent_id)
        result = stack.engine.run(intent, evidence_message_id=msg)
        cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
        assert cal["state"] == L.VERIFIED
        assert result.committed is True
        events = stack.apps.calendar.search_candidates(start_iso=TUESDAY, title=title)
        assert len(events) == 1, "a takeover must not re-create an effect that exists"
    finally:
        stack.close()


def test_post_commit_duplicate_is_flagged_not_undone(tmp_path):
    stack, msg, intent, title = _crash_stack(tmp_path, "India")
    try:
        result = stack.engine.run(intent, evidence_message_id=msg)
        assert result.committed is True
        stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                          attendees=intent.scope.recipients)
        findings = stack.engine.post_commit_scan(intent)
        assert findings and findings[0]["effect_id"] == "CALENDAR-01"
        assert findings[0]["candidates"] == 2
        row = stack.ledger.get(intent.intent_hash, "CALENDAR-01")
        assert row["state"] == L.POST_COMMIT_DUPLICATE
        # the commit stands: the effect really existed when it was made
        assert stack.registry.get(intent.intent_id)["status"] == "COMMITTED"
        events = [e.get("event_type") for e in stack.audit.timeline(intent.intent_id)]
        assert "POST_COMMIT_DUPLICATE" in events
    finally:
        stack.close()
