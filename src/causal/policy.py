"""The policy engine: every dangerous transition goes through here.

The model cannot call these. Nothing in the adapter layer may decide to act;
adapters only carry out what policy has already permitted. If you find an
authorization check outside this file, that is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .intent import ALLOWED_OPERATIONS, Intent
from .ledger import NOT_FOUND, UNKNOWN, VERIFIED, AMBIGUOUS

# refusal codes — every refusal in the system is one of these
EVIDENCE_MISSING = "EVIDENCE_MISSING"
EVIDENCE_STALE = "EVIDENCE_STALE"
AUTHORITY_MISMATCH = "AUTHORITY_MISMATCH"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
CONFLICT = "CONFLICT"
STALE_AUTHORIZATION = "STALE_AUTHORIZATION"
POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
AMBIGUOUS_EXTERNAL_STATE = "AMBIGUOUS_EXTERNAL_STATE"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
IDEMPOTENT = "IDEMPOTENT"
SUPERSEDED = "SUPERSEDED"
NOT_PROVABLE = "NOT_PROVABLE"
POST_COMMIT_DUPLICATE = "POST_COMMIT_DUPLICATE"
EXCLUSIVITY_CONFLICT = "EXCLUSIVITY_CONFLICT"
AWAITING_APPROVAL = "AWAITING_APPROVAL"

# Which surfaces reach the outside world. Effects here wait for a named human; every
# other effect stays autonomous. Empty by default: this is the operator's declaration
# about their own systems, not a default this project should impose. The console's
# sign-off sequence declares {"slack"} to demonstrate it.
SIGNOFF_REQUIRED_APPS: set[str] = {"slack"}


@dataclass
class Decision:
    allow: bool
    code: str = "ALLOWED"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"allow": self.allow, "code": self.code, "reasons": list(self.reasons)}


def can_change_authority(intent: Intent, *, frozen: bool) -> Decision:
    """Frozen authority cannot be reinterpreted, by anyone, ever."""
    if frozen:
        return Decision(False, "AUTHORITY_FROZEN",
                        ["authority is frozen for this intent and may not be reinterpreted"])
    return Decision(True)


def can_execute(intent: Intent, effect, *, effect_state: str, now_ts: float,
                authority_frozen: bool, conflict_clear: bool) -> Decision:
    reasons: list[str] = []
    if not authority_frozen:
        reasons.append("authority has not been frozen")
    if intent.expires_at_ts and now_ts > intent.expires_at_ts:
        return Decision(False, STALE_AUTHORIZATION, ["intent expired before execution"])
    if not conflict_clear:
        reasons.append("an active conflicting intent holds this business outcome")
    if effect.app not in intent.scope.allowed_apps:
        reasons.append(f"app {effect.app!r} is not in the frozen scope")
    if effect.operation not in ALLOWED_OPERATIONS.get(effect.app, set()):
        reasons.append(f"operation {effect.operation!r} is not permitted for app {effect.app!r}")
    if not intent.scope.allows(customer=intent.scope.customer, project=intent.scope.project,
                               event=intent.scope.event):
        reasons.append("effect subject is outside the frozen scope")
    if effect_state == AMBIGUOUS:
        reasons.append("effect state is AMBIGUOUS; a human must resolve it")
    if effect_state == VERIFIED:
        reasons.append("effect is already VERIFIED; a duplicate write is forbidden")
    if reasons:
        return Decision(False, OUT_OF_SCOPE, reasons)
    return Decision(True)


def can_retry(effect_state: str) -> Decision:
    """UNKNOWN is never retried blindly. Only NOT_FOUND may be executed again."""
    if effect_state == NOT_FOUND:
        return Decision(True)
    if effect_state == UNKNOWN:
        return Decision(False, AMBIGUOUS_EXTERNAL_STATE,
                        ["outcome unknown; reconcile before any retry"])
    if effect_state == VERIFIED:
        return Decision(False, "ALREADY_VERIFIED", ["already verified; refusing a duplicate write"])
    return Decision(False, "NOT_RETRYABLE", [f"state {effect_state} is not retryable"])


def can_commit(*, authorized: bool, authority_frozen: bool, preconditions_ok: bool,
               conflict_clear: bool, required_effects: list[dict], now_ts: float,
               expires_at_ts: float | None) -> Decision:
    """The single commit rule. No partial commits, no warnings-as-success."""
    reasons: list[str] = []
    if not authorized:
        reasons.append("intent is not authorized")
    if not authority_frozen:
        reasons.append("authority is not frozen")
    if not preconditions_ok:
        reasons.append("a precondition is unsatisfied")
    if not conflict_clear:
        reasons.append("an active conflict exists")
    if expires_at_ts and now_ts > expires_at_ts:
        reasons.append("intent has expired")
    for e in required_effects:
        if e.get("state") != VERIFIED:
            reasons.append(f"required effect {e.get('effect_id')} is {e.get('state')}, not VERIFIED")
    if reasons:
        # Name the refusal precisely. "NOT_COMMITTED" tells the operator nothing that
        # the effect states do not already say, and the two interesting cases are
        # distinguishable: an effect whose ownership is contested, versus one whose
        # absence could not be established.
        states = {e.get("state") for e in required_effects if e.get("state") != VERIFIED}
        if "AMBIGUOUS" in states:
            return Decision(False, AMBIGUOUS_EXTERNAL_STATE, reasons)
        if "NOT_PROVABLE" in states:
            return Decision(False, NOT_PROVABLE, reasons)
        if "AWAITING_APPROVAL" in states:
            return Decision(False, AWAITING_APPROVAL, reasons)
        return Decision(False, "NOT_COMMITTED", reasons)
    return Decision(True, "COMMITTED")
