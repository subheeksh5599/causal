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


def test_an_unknown_state_still_reads_as_something():
    assert plain.phrase_effect("SOMETHING_NEW") == "something new"


def test_a_person_is_told_what_is_waiting_on_them():
    view = plain.job_view(intent_id="X", status="BLOCKED", refusal_code="AWAITING_APPROVAL",
                          effects=[{"app": "calendar", "effect_id": "CALENDAR-01", "state": "VERIFIED"},
                                   {"app": "slack", "effect_id": "SLACK-01", "state": AWAITING_APPROVAL}],
                          approvals=[], committed=False)
    assert "SLACK-01" in view["headline"]
    assert view["can_approve"] is True
    assert view["confirmed"] == 1 and view["of"] == 2


def test_the_contested_case_is_not_softened():
    view = plain.job_view(intent_id="X", status="BLOCKED", refusal_code="AMBIGUOUS_EXTERNAL_STATE",
                          effects=[{"app": "calendar", "effect_id": "CALENDAR-01", "state": "AMBIGUOUS"}],
                          approvals=[], committed=False)
    assert view["headline"] == "Two identical things exist. I stopped rather than guess."
    assert view["can_approve"] is False


def test_a_confirmed_job_reads_as_confirmed():
    view = plain.job_view(intent_id="X", status="COMMITTED", refusal_code="",
                          effects=[{"app": a, "effect_id": f"{a}-01", "state": "VERIFIED"}
                                   for a in ("calendar", "linear", "slack")],
                          approvals=[], committed=True)
    assert view["headline"] == "Done. 3 of 3 systems confirmed."


def test_no_phrase_promises_more_than_the_state_supports():
    """A translation that reads better than the truth is a bug, not a kindness."""
    for state, phrase in plain.EFFECT.items():
        if state != VERIFIED:
            assert "confirmed by reading it back" not in phrase


# -- the API --------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUSAL_DB", str(tmp_path / "review-console.db"))
    monkeypatch.setenv("CAUSAL_MODE", "LOCAL")
    import causal.api as api
    api._state["stack"] = None
    api._origin.clear()
    with TestClient(api.app) as c:
        yield c
    api._state["stack"] = None


def test_the_review_page_is_served(client):
    body = client.get("/review")
    assert body.status_code == 200
    assert "Jobs" in body.text
    assert "/api/jobs" in body.text


def test_jobs_are_empty_before_anything_runs(client):
    body = client.get("/api/jobs").json()
    assert body["jobs"] == []
    assert body["need_approval"] == []


def test_a_waiting_job_is_listed_for_the_person(client):
    client.post("/api/run/awaiting_signoff")
    body = client.get("/api/jobs").json()
    waiting = body["need_approval"]
    assert len(waiting) == 1
    assert "waiting for your approval" in waiting[0]["headline"].lower()
    assert waiting[0]["can_approve"] is True
    assert waiting[0]["sequence"] == "awaiting_signoff"


def test_approving_without_a_name_is_refused(client):
    client.post("/api/run/awaiting_signoff")
    job = client.get("/api/jobs").json()["need_approval"][0]
    r = client.post("/api/approve", json={"intent_id": job["intent_id"], "approved_by": "  "})
    assert r.status_code == 400
    assert "named human" in r.json()["detail"]


def test_approving_an_unknown_job_is_404(client):
    r = client.post("/api/approve", json={"intent_id": "NOPE", "approved_by": "dana"})
    assert r.status_code == 404


def test_approving_something_that_awaits_nothing_is_409(client):
    client.post("/api/run/intended")
    job = client.get("/api/jobs").json()["jobs"][0]
    r = client.post("/api/approve", json={"intent_id": job["intent_id"], "approved_by": "dana"})
    assert r.status_code == 409


def test_approving_through_the_api_commits_the_job(client):
    client.post("/api/run/awaiting_signoff")
    job = client.get("/api/jobs").json()["need_approval"][0]
    r = client.post("/api/approve",
                    json={"intent_id": job["intent_id"], "approved_by": "dana.reyes@acme.example"})
    assert r.status_code == 200
    assert r.json()["resumed"] == "awaiting_signoff"
    assert r.json()["result"]["committed"] is True

    after = client.get("/api/jobs").json()
    assert after["need_approval"] == []
    done = [j for j in after["jobs"] if j["intent_id"] == job["intent_id"]][0]
    assert done["headline"].startswith("Done.")
    assert done["approved_by"] == ["dana.reyes@acme.example"]
    assert r.json()["metrics"]["false_commits"] == 0


def test_no_credential_appears_in_the_review_surfaces(client):
    """The new endpoints get the same scrutiny as the old ones."""
    import os
    client.post("/api/run/all")
    client.post("/api/approve", json={"intent_id": "NOPE", "approved_by": "dana"})
    client.post("/api/intake", json={
        "text": "Acme approved the renewal. Schedule the renewal kickoff 2026-10-07T15:00."})
    body = (json.dumps(client.get("/api/jobs").json())
            + client.get("/review").text
            + json.dumps(client.post("/api/intake", json={
                "text": "Acme approved the renewal.", "adversarial": True}).json()))
    for name, value in os.environ.items():
        if len(value) >= 12 and any(k in name.upper() for k in
                                    ("KEY", "SECRET", "TOKEN", "CLIENT_ID")):
            assert value not in body, f"{name} leaked into the review surface"


# -- the intake, through the API -----------------------------------------

REQUEST = ("Acme approved the renewal. Schedule the renewal kickoff "
           "2026-10-07T15:00 and open the renewal task.")


def test_intake_turns_a_request_in_words_into_a_contract(client):
    body = client.post("/api/intake", json={"text": REQUEST}).json()
    assert body["accepted"] is True, body["reasons"]
    intent = body["intent"]
    assert intent["conflict_key"]
    assert intent["effects"]
    # the authority mapping is the operator's, and every app in it is read-only
    assert intent["authority"]["work"] == "linear"
    assert intent["authority"]["meeting"] == "calendar"


def test_intake_refuses_a_proposal_that_widens_its_own_authority(client):
    body = client.post("/api/intake", json={"text": REQUEST, "adversarial": True}).json()
    assert body["accepted"] is False
    assert body["intent"] is None
    joined = " ".join(body["reasons"]).lower()
    assert "authority" in joined and "operator" in joined
    # and the recipients it tried to widen are refused too
    assert "notified" in joined or "recipients" in joined


def test_intake_needs_a_request(client):
    assert client.post("/api/intake", json={"text": "   "}).status_code == 400


def test_the_engine_class_is_the_one_the_console_uses(client):
    """A review surface that drives a different engine is a mock, not a surface."""
    import causal.api as api
    assert isinstance(api.stack().engine, Causal)


# -- the model metric is a measurement, not a typed-in zero ----------------

def test_commits_decided_by_a_model_is_summed_from_the_store(client):
    """The reported number must equal what the outcomes table actually holds.

    This is what stops the line being a hardcoded zero: it is an aggregate over
    persisted per-run counters, and a run that consulted a hook would move it.
    """
    import causal.api as api
    client.post("/api/run/all")
    reported = client.get("/api/state").json()["metrics"]["commits_decided_by_a_model"]
    rows = api.stack().outcomes.all()
    assert reported == sum(r["model_calls"] for r in rows)
    assert all(r["model_calls"] == 0 for r in rows), "a shipped sequence consulted a model"


def test_an_attached_model_hook_is_never_consulted_by_the_engine(tmp_path):
    """Falsification. If this counter could only ever read zero, it would prove nothing."""
    stack = S.fresh_stack("LOCAL", str(tmp_path / "hooked.db"))
    planner = S.LyingPlanner()
    stack.engine.planner = planner

    msg = S.seed_approval(stack, "Margie", "renewal", S.TUESDAY, "msg-hook")
    intent = S.build_binding_intent("Margie", "renewal", S.TUESDAY, intent_id="C-HOOK")
    first = stack.engine.run(intent, evidence_message_id=msg)
    assert first.committed is True
    assert planner.calls == 0, "the decision path consulted a model"
    assert first.model_calls == 0

    # Now consult it by hand, to prove the number tracks the hook rather than a constant.
    planner.propose()
    msg2 = S.seed_approval(stack, "Dana", "rollout", S.TUESDAY, "msg-hook-2")
    intent2 = S.build_binding_intent("Dana", "rollout", S.TUESDAY, intent_id="C-HOOK-2")
    second = stack.engine.run(intent2, evidence_message_id=msg2)
    assert second.model_calls == 1, "the counter does not reflect the hook's calls"

    # And a third run, untouched, leaves it where it was: the engine adds nothing.
    msg3 = S.seed_approval(stack, "Priya", "cutover", S.TUESDAY, "msg-hook-3")
    intent3 = S.build_binding_intent("Priya", "cutover", S.TUESDAY, intent_id="C-HOOK-3")
    third = stack.engine.run(intent3, evidence_message_id=msg3)
    assert third.model_calls == 1
