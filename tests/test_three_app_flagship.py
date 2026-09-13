"""The three-app flagship: Gmail authorises, Calendar schedules, Linear works.

Slack is deliberately absent. The notification effect belonged to the four-app
variant; without it the "successful but unauthorised" demonstration is carried
by the Calendar effect instead, which is arguably stronger — the disputed fact
(the time) lives in the calendar object itself.
"""

from __future__ import annotations

from causal import EffectSpec

from .conftest import make_intent, seed_approval

CALENDAR_ONLY_WRONG = (
    EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT",
               payload=(("start_iso", "2026-09-16T16:00"),)),   # Wednesday, not Tuesday
    EffectSpec("LINEAR-01", "linear", "CREATE_ISSUE"),
)


def test_calendar_payload_that_contradicts_the_scope_is_not_committed(stack):
    """The API accepts the event. The business postcondition does not hold.

    The contract is correct — the scope says Tuesday 15:00, the evidence says
    Tuesday 15:00. What the model proposed for the effect says Wednesday 16:00.
    The write succeeds. The effect must not become VERIFIED, and the intent must
    not commit.
    """
    seed_approval(stack.apps)
    intent = make_intent(effects=CALENDAR_ONLY_WRONG)
    result = stack.engine.run(intent, evidence_message_id="msg-1")

    cal = next(e for e in result.effects if e["effect_id"] == "CALENDAR-01")
    assert cal["state"] == "VERIFICATION_FAILED"
    assert result.committed is False
    assert result.refusal_code == "NOT_COMMITTED"

    # the write really did happen, exactly once, and the artifact really is wrong
    assert len(stack.apps.calendar.world.events) == 1
    event = stack.apps.calendar.world.events[0]
    assert event["start_iso"] == "2026-09-16T16:00"
    assert event["start_iso"] != intent.scope.start_iso


def test_the_linear_effect_still_verifies_when_the_calendar_one_fails(stack):
    """No partial commit, but also no cascading false failure."""
    seed_approval(stack.apps)
    result = stack.engine.run(make_intent(effects=CALENDAR_ONLY_WRONG), evidence_message_id="msg-1")
    states = {e["effect_id"]: e["state"] for e in result.effects}
    assert states["LINEAR-01"] == "VERIFIED"
    assert states["CALENDAR-01"] == "VERIFICATION_FAILED"
    assert result.committed is False


def test_a_correct_payload_commits_with_two_effects(stack):
    """The happy path for the three-app flagship: 2 effects, all verified."""
    seed_approval(stack.apps)
    intent = make_intent(effects=(
        EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT"),
        EffectSpec("LINEAR-01", "linear", "CREATE_ISSUE"),
    ))
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    assert result.committed is True
    assert {e["effect_id"]: e["state"] for e in result.effects} == {
        "CALENDAR-01": "VERIFIED", "LINEAR-01": "VERIFIED"}


def test_a_temporal_claim_in_the_task_title_disagrees_and_is_caught(stack):
    seed_approval(stack.apps)
    intent = make_intent(effects=(
        EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT"),
        EffectSpec("LINEAR-01", "linear", "CREATE_ISSUE",
                   payload=(("title", "Kickoff moved to Wednesday 16:00"),)),
    ))
    result = stack.engine.run(intent, evidence_message_id="msg-1")
    lin = next(e for e in result.effects if e["effect_id"] == "LINEAR-01")
    assert lin["state"] == "VERIFICATION_FAILED"
    assert result.committed is False
