"""The orchestrator. This is where the protocol lives.

Order is fixed and non-negotiable:

    expiry -> evidence gate -> authority check -> freeze -> scope check ->
    conflict claim -> plan -> execute -> independent verify -> reconcile ->
    commit gate

Two rules the code enforces structurally rather than by convention:

1. The LLM cannot reach any state transition. There is no parameter through
   which a model opinion can arrive; the engine reads apps and the ledger.
2. A write happens only from PLANNED or NOT_FOUND. UNKNOWN reconciles first.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any
from . import audit as A
from . import policy
from .apps import Apps, PermanentError, TransientError
from .evidence_sink import OutcomeStore
from .intent import Intent, parse_approval, conflict_key_for
from . import binding
from .ledger import (AMBIGUOUS, AWAITING_APPROVAL, EFFECTED, NOT_FOUND, NOT_PROVABLE, PLANNED,
                     POST_COMMIT_DUPLICATE, RECONCILING, REJECTED, REQUESTED, UNKNOWN, VERIFIED,
                     VERIFYING, VERIFICATION_FAILED, EffectLedger, IllegalTransition)
from .postconditions import check
from .reconcile import AMBIGUOUS as R_AMBIGUOUS
from .reconcile import action_for, reconcile
