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

