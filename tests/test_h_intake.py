"""Group N - intake: a model may propose, and cannot widen what the system accepts.

The claim this file tests is narrow and checkable: swap the proposer for a model and
the set of acceptable contracts does not change. Every rejection below is produced by
deterministic code, and the "model" proposers here are deliberately hostile.
"""

from __future__ import annotations

import pytest

from causal import intake
from causal import ledger as L
from causal.intake import Outcome, RuleProposer, compile_intent
from causal.scenarios import TUESDAY, fresh_stack, seed_approval

REQUEST = f"Acme approved the renewal. Kickoff {TUESDAY}."
# the evidence gate looks for the customer and project inside the message body
EVIDENCE = f"Acme approved the Renewal. Kickoff {TUESDAY}."


class HostileProposer:
    """Offers whatever it is told to offer, standing in for a model that has been
    prompted into trouble or is simply wrong."""

    name = "hostile-model"
    is_model = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def propose(self, text: str) -> dict:
        return self.payload


def good_proposal() -> dict:
    return RuleProposer().propose(REQUEST)


# ------------------------------------------------------------------- acceptance

def test_the_rule_proposer_turns_a_sentence_into_a_frozen_intent():
    outcome = compile_intent(REQUEST, proposer=RuleProposer(), intent_id="C-NL-1")
    assert outcome.accepted, outcome.reasons
    intent = outcome.intent
    assert intent.scope.customer == "Acme"
    assert intent.scope.project == "Renewal"
    assert intent.scope.start_iso == TUESDAY
    assert intent.intent_hash, "the intent must be frozen at intake, not later"
    assert [e.effect_id for e in intent.effects] == ["CALENDAR-01", "LINEAR-01", "SLACK-01"]


def test_authority_comes_from_the_operator_not_the_proposal():
    outcome = compile_intent(REQUEST, proposer=RuleProposer(), intent_id="C-NL-2")
    assert outcome.intent.authority == intake.SYSTEM_AUTHORITY
    assert outcome.intent.authority["approval"] == "gmail"


def test_a_model_proposer_producing_the_same_contract_is_accepted_the_same_way():
    """"Swap the proposer" must not change the contract's identity."""
    from_rule = compile_intent(REQUEST, proposer=RuleProposer(), intent_id="C-NL-3")
    from_model = compile_intent(REQUEST, proposer=HostileProposer(good_proposal()),
                                intent_id="C-NL-3")
    assert from_rule.accepted and from_model.accepted
    assert from_rule.intent.intent_hash == from_model.intent.intent_hash
    assert from_rule.intent.authority == from_model.intent.authority


def test_intake_flows_into_the_engine_and_commits(tmp_path):
    """The end-to-end point: a sentence in, three effects verified out."""
    stack = fresh_stack("LOCAL", str(tmp_path / "nl.db"))
    try:
        seed_approval(stack, "Acme", "Renewal", TUESDAY, "msg-nl")
        outcome = compile_intent(REQUEST, proposer=RuleProposer(), intent_id="C-NL-4")
        assert outcome.accepted, outcome.reasons
        result = stack.engine.run(outcome.intent, evidence_message_id="msg-nl")
        assert result.committed is True, result.reasons
        states = {e["effect_id"]: e["state"] for e in result.effects}
        assert set(states.values()) == {L.VERIFIED}
    finally:
        stack.close()


# ------------------------------------------------------------------- rejections

@pytest.mark.parametrize("field_name", ["authority", "conflict_key", "intent_hash", "status"])
def test_a_proposal_may_not_set_reserved_fields(field_name):
    payload = good_proposal() | {field_name: {"approval": "slack"}}
    outcome = compile_intent(REQUEST, proposer=HostileProposer(payload))
    assert not outcome.accepted
    assert any(field_name in reason for reason in outcome.reasons), outcome.reasons


def test_a_proposal_may_not_declare_its_own_evidence_requirements():
    payload = good_proposal()
    payload["effects"][0]["postconditions"] = ["whatever the model decides"]
    outcome = compile_intent(REQUEST, proposer=HostileProposer(payload))
    assert not outcome.accepted
    assert any("postconditions" in r for r in outcome.reasons), outcome.reasons


def test_a_proposal_may_not_reach_for_an_app_outside_the_allowlist():
    payload = good_proposal()
    payload["effects"].append({"effect_id": "STRIPE-01", "app": "stripe",
                               "operation": "CREATE_PAYMENT"})
    outcome = compile_intent(REQUEST, proposer=HostileProposer(payload))
    assert not outcome.accepted
    assert any("stripe" in r for r in outcome.reasons), outcome.reasons


def test_a_proposal_may_not_invent_an_operation():
    payload = good_proposal()
    payload["effects"][1]["operation"] = "DELETE_ISSUE"
    outcome = compile_intent(REQUEST, proposer=HostileProposer(payload))
    assert not outcome.accepted
    assert any("DELETE_ISSUE" in r for r in outcome.reasons), outcome.reasons


def test_an_unparseable_request_is_declined_rather_than_guessed():
    outcome = compile_intent("hello, can you sort the thing out please?",
                             proposer=RuleProposer())
    assert not outcome.accepted
    assert outcome.reasons == ["the proposer offered nothing parseable"]


def test_a_proposal_missing_the_time_is_rejected():
    payload = good_proposal()
    payload.pop("start_iso")
    outcome = compile_intent(REQUEST, proposer=HostileProposer(payload))
    assert not outcome.accepted
    assert any("start_iso" in r for r in outcome.reasons), outcome.reasons


def test_a_model_that_fails_transport_is_reported_not_swallowed():
    class Broken:
        name = "broken-model"
        is_model = True

        def propose(self, text):
            raise RuntimeError("model endpoint returned 503")

    outcome = compile_intent(REQUEST, proposer=Broken())
    assert not outcome.accepted
    assert "503" in outcome.reasons[0]


def test_the_outcome_always_says_which_proposer_ran():
    """A run must never be ambiguous about whether a model was involved."""
    rule = compile_intent(REQUEST, proposer=RuleProposer())
    model = compile_intent(REQUEST, proposer=HostileProposer(good_proposal()))
    assert rule.proposer == "rule-based"
    assert model.proposer == "hostile-model"
    assert rule.as_dict()["accepted"] is True
    assert rule.as_dict()["proposal"], "the raw proposal is kept for audit"
