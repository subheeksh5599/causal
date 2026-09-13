"""CAUSAL - an intent-bound execution layer.

An external action is not successful because the artifact exists. It is
successful only when it is provably the authorized, conflict-free consequence
of one frozen intent.

    EFFECTED  !=  VERIFIED  !=  COMMITTED

The model proposes. Deterministic policy authorizes. Independent readers verify.
Ambiguous outcomes reconcile before any retry. Only then can an intent commit.
"""

from .audit import Audit
from .engine import Causal, RunResult
from .evidence_sink import OutcomeStore
from .intent import (ALLOWED_OPERATIONS, EffectSpec, Evidence, Intent, Proposal, Scope,
                     conflict_key_for, parse_approval, validate_proposal)
from .ledger import (AMBIGUOUS, EFFECTED, NOT_FOUND, PLANNED, RECONCILING, REJECTED, REQUESTED,
                     UNKNOWN, VERIFIED, VERIFYING, VERIFICATION_FAILED, EffectLedger,
                     IllegalTransition)
from .policy import Decision, can_commit, can_execute, can_retry, can_change_authority
from .reconcile import reconcile
from .registry import CONFLICT, IDEMPOTENT, REGISTERED, SUPERSEDE, Registry

__all__ = [
    "AMBIGUOUS", "Audit", "CONFLICT", "Causal", "Decision", "EFFECTED", "EffectLedger",
    "EffectSpec", "Evidence", "IDEMPOTENT", "IllegalTransition", "Intent", "NOT_FOUND",
    "OutcomeStore", "PLANNED", "Proposal", "RECONCILING", "REGISTERED", "REJECTED",
    "REQUESTED", "Registry", "RunResult", "SUPERSEDE", "Scope", "UNKNOWN", "VERIFIED",
    "VERIFYING", "VERIFICATION_FAILED", "can_change_authority", "can_commit", "can_execute",
    "can_retry", "conflict_key_for", "parse_approval", "reconcile", "validate_proposal",
]
