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
from .registry import CONFLICT, IDEMPOTENT, REGISTERED, RESUME, SUPERSEDE, Registry

ATTEMPTS_PER_EFFECT = 3


@dataclass
class RunResult:
    intent_id: str
    intent_hash: str
    status: str
    committed: bool
    refusal_code: str = ""
    reasons: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    effects: list[dict] = field(default_factory=list)
    reconciliations: list[dict] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)
    writes_attempted: int = 0
    #: how many times a model hook was consulted during this run. The engine has no
    #: call site for one, so this is 0 in practice — but it is read off the attached
    #: planner rather than typed in, so a refactor that reintroduces a consultation
    #: moves it and the aggregate in `/api/state` moves with it.
    model_calls: int = 0

    def as_dict(self) -> dict:
        return {"intent_id": self.intent_id, "intent_hash": self.intent_hash, "status": self.status,
                "committed": self.committed, "refusal_code": self.refusal_code,
                "reasons": self.reasons, "evidence": self.evidence,
                "effects": [{k: v for k, v in e.items() if k != "evidence"} for e in self.effects],
                "reconciliations": self.reconciliations, "writes_attempted": self.writes_attempted,
                "model_calls": self.model_calls}


class Causal:
    #: per-surface matchability overrides, for an operator who disagrees with the
    #: defaults in binding.py. Empty means "use the table as shipped".
    matchability_overrides: dict[str, str] = {}

    #: which surfaces reach the outside world and therefore wait for a named human.
    #: None means "use policy.SIGNOFF_REQUIRED_APPS"; this is the operator's call.
    signoff_apps: set[str] | None = None

    #: an optional model hook, attached only so that consultation can be COUNTED.
    #: Nothing in the decision path calls it — tests assert the count stays at 0, and
    #: the number reported by the API is this object's own call counter, aggregated.
    planner: object | None = None

    def __init__(self, apps: Apps, registry: Registry, ledger: EffectLedger, audit: A.Audit,
                 outcomes: OutcomeStore | None = None, *, known_contacts: set[str] | None = None,
                 reference_date_iso: str | None = None) -> None:
        self.apps = apps
        self.registry = registry
        self.ledger = ledger
        self.audit = audit
        self.outcomes = outcomes
        self.known_contacts = known_contacts or set()
        self.reference_date_iso = reference_date_iso

    # -- public ---------------------------------------------------------
    def run(self, intent: Intent, *, evidence_message_id: str, now_ts: float | None = None) -> RunResult:
        now = time.time() if now_ts is None else now_ts
        result = RunResult(intent.intent_id, "", "RUNNING", False)
        self._takeover = False      # set when this run takes an intent over from a dead worker
        self.audit.record(intent_id=intent.intent_id, event_type=A.INTENT_CREATED,
                          reason="intent received", metadata={"request": intent.request})

        # 1. expiry before anything else
        if intent.expires_at_ts and now > intent.expires_at_ts:
            return self._refuse(result, intent, policy.STALE_AUTHORIZATION,
                                ["intent expired before execution"])

        # 2. evidence gate (deterministic; no model)
        try:
            message = self.apps.mail.read_message(evidence_message_id)
        except PermanentError as exc:
            self.audit.record(intent_id=intent.intent_id, event_type=A.EVIDENCE_REJECTED,
                              reason=str(exc))
            return self._refuse(result, intent, policy.EVIDENCE_MISSING,
                                [f"evidence source unreadable: {exc}"])

        ev = parse_approval(message, known_contacts=self.known_contacts,
                            expected_customer=intent.scope.customer,
                            expected_project=intent.scope.project,
                            reference_date_iso=self.reference_date_iso, now_ts=now)
        result.evidence = ev.as_dict()
        if not ev.ok:
            self.audit.record(intent_id=intent.intent_id, event_type=A.EVIDENCE_REJECTED,
                              reason="; ".join(ev.reasons), metadata=ev.as_dict())
            return self._refuse(result, intent, policy.EVIDENCE_MISSING, ev.reasons)
        if intent.approval_max_age_s and ev.observed_at:
            age = now - ev.observed_at
            if age > intent.approval_max_age_s:
                self.audit.record(intent_id=intent.intent_id, event_type=A.EVIDENCE_REJECTED,
                                  reason="approval is stale")
                return self._refuse(result, intent, policy.EVIDENCE_STALE,
                                    [f"approval is {int(age)}s old"])
        if ev.observed_at and ev.observed_at > now + 300:
            self.audit.record(intent_id=intent.intent_id, event_type=A.EVIDENCE_REJECTED,
                              reason="approval timestamp is in the future")
            return self._refuse(result, intent, policy.EVIDENCE_MISSING,
                                ["approval is dated in the future"])
        self.audit.record(intent_id=intent.intent_id, event_type=A.EVIDENCE_ACCEPTED,
                          reason="deterministic checks passed", metadata=ev.as_dict())

        # 3. authority check: the model may not move the disputed fact
        if ev.stated_start_iso != intent.scope.start_iso:
            return self._refuse(result, intent, policy.AUTHORITY_MISMATCH,
                                [f"authority {self._authority_for(intent, 'time')!r} states "
                                 f"{ev.stated_start_iso!r}; intent proposes {intent.scope.start_iso!r}"])

        # 4. freeze
        intent.freeze()
        result.intent_hash = intent.intent_hash
        self.audit.record(intent_id=intent.intent_id, event_type=A.AUTHORITY_FROZEN,
                          intent_hash=intent.intent_hash, new_state="AUTHORIZED",
                          reason="authority snapshot frozen",
                          metadata={"authority": dict(intent.authority),
                                    "authority_hash": intent.authority_hash})

        # 5. scope check on every effect BEFORE any write
        for spec in intent.effects:
            if spec.app not in intent.scope.allowed_apps:
                return self._refuse(result, intent, policy.OUT_OF_SCOPE,
                                    [f"app {spec.app!r} outside the frozen scope"])
            if spec.operation not in policy.ALLOWED_OPERATIONS.get(spec.app, set()):
                return self._refuse(result, intent, policy.UNSUPPORTED_OPERATION,
                                    [f"operation {spec.operation!r} is not permitted "
                                     f"for app {spec.app!r}"])
            if spec.target and intent.scope.recipients and spec.target not in intent.scope.recipients:
                return self._refuse(result, intent, policy.OUT_OF_SCOPE,
                                    [f"target {spec.target!r} is not an authorised recipient"])

        # 6. conflict claim
        outcome = self.registry.register(intent_id=intent.intent_id,
                                         conflict_key=intent.conflict_key,
                                         intent_hash=intent.intent_hash)
        if outcome["outcome"] == CONFLICT:
            self.audit.record(intent_id=intent.intent_id, event_type=A.CONFLICT_DETECTED,
                              intent_hash=intent.intent_hash, reason=outcome["why"],
                              metadata={"holder": outcome["holder"]})
            return self._refuse(result, intent, policy.CONFLICT,
                                [f"conflicts with active intent "
                                 f"{(outcome['holder'] or {}).get('intent_id')}"])
        if outcome["outcome"] == SUPERSEDE:
            self.audit.record(intent_id=intent.intent_id, event_type=A.CONFLICT_DETECTED,
                              reason=outcome["why"],
                              metadata={"supersedes": outcome["holder"]})
            result.status, result.reasons = "FROZEN", [outcome["why"]]
            result.refusal_code = policy.SUPERSEDED
            result.intent_hash = intent.intent_hash
            self._finish(intent, result)
            return result
        if outcome["outcome"] == IDEMPOTENT:
            self.audit.record(intent_id=intent.intent_id, event_type=A.CONFLICT_CLEAR,
                              reason="identical intent already active or committed")
            result.status, result.reasons = "IDEMPOTENT", [outcome["why"]]
            # Not a commit, so it must still say why: a status alone is not a reason.
            result.refusal_code = policy.IDEMPOTENT
            result.effects, result.reconciliations = self._snapshot(intent)
            self._finish(intent, result)
            return result
        if outcome["outcome"] == RESUME:
            self._takeover = True
            self.audit.record(intent_id=intent.intent_id, event_type=A.CONFLICT_CLEAR,
                              intent_hash=intent.intent_hash, reason=outcome["why"])
        # 6b. scope identity, then the authored exclusivity relation. A different
        # conflict key can still be the same contract: "renew" and "cancel" are not
        # duplicates of each other, so the unique index above can never catch them.
        resource = intent.scope.resource or intent.scope.project
        operation = intent.scope.operation or "default"
        self.registry.note_scope_identity(intent.intent_id, resource=resource,
                                          operation=operation)
        holder = self.registry.exclusive_holder(resource=resource, operation=operation,
                                                exclude_intent_id=intent.intent_id)
        if holder is not None:
            self.audit.record(intent_id=intent.intent_id, event_type=A.EXCLUSIVITY_BLOCKED,
                              intent_hash=intent.intent_hash,
                              reason=f"mutually exclusive with {holder['intent_id']}",
                              metadata={"holder": holder["intent_id"],
                                        "holder_operation": holder.get("operation", ""),
                                        "resource": resource,
                                        "policy_digest": binding.EXCLUSIVITY_DIGEST})
            return self._refuse(result, intent, policy.EXCLUSIVITY_CONFLICT,
                                [f"{operation} cannot be active while "
                                 f"{holder.get('operation')} holds {resource}"])
        self.audit.record(intent_id=intent.intent_id, event_type=A.CONFLICT_CLEAR,
                          intent_hash=intent.intent_hash, reason="no active intent for this outcome")

        # 7. plan
        for spec in intent.effects:
            self.ledger.plan(intent_hash=intent.intent_hash, intent_id=intent.intent_id,
                             effect_id=spec.effect_id, app=spec.app, operation=spec.operation,
                             idempotency_key=f"{intent.intent_hash}::{spec.effect_id}")

        # 8. drive every effect
        for spec in intent.effects:
            self._drive(intent, spec.effect_id, now)

        # 9a. re-validate every binding immediately before the gate. Binding is not a
        # one-time act: a duplicate can appear between the write and the commit, and a
        # commit made on a stale binding is exactly the false assurance this refuses.
        for spec in intent.effects:
            row = self.ledger.get(intent.intent_hash, spec.effect_id)
            if row is None or row["state"] != VERIFIED:
                continue
            verdict = self._bind(intent, spec)
            if verdict.outcome == binding.AMBIGUOUS:
                self.ledger.transition(intent.intent_hash, spec.effect_id, AMBIGUOUS,
                                       evidence={"stage": "pre-commit revalidation",
                                                 "why": verdict.why})
                self.audit.record(intent_id=intent.intent_id, event_type=A.DUPLICATE_DETECTED,
                                  intent_hash=intent.intent_hash, new_state=AMBIGUOUS,
                                  reason=f"pre-commit revalidation of {spec.effect_id}: "
                                         f"{verdict.why}",
                                  metadata={"candidates": len(verdict.candidates)})

        # 9. commit gate
        result.effects, result.reconciliations = self._snapshot(intent)
        decision = policy.can_commit(
            authorized=intent.status in ("AUTHORIZED", "EXECUTING"),
            authority_frozen=bool(intent.intent_hash),
            preconditions_ok=ev.ok,
            conflict_clear=True,
            required_effects=result.effects,
            now_ts=time.time(),
            expires_at_ts=intent.expires_at_ts,
        )
        self.audit.record(intent_id=intent.intent_id, event_type=A.COMMIT_EVALUATED,
                          intent_hash=intent.intent_hash, reason=decision.code,
                          metadata={"reasons": decision.reasons})
        if decision.allow:
            result.status, result.committed = "COMMITTED", True
            self.audit.record(intent_id=intent.intent_id, event_type=A.COMMITTED,
                              intent_hash=intent.intent_hash, new_state="COMMITTED",
                              reason="every required effect independently verified")
            self.registry.set_status(intent.intent_id, "COMMITTED")
        else:
            result.status, result.committed = "BLOCKED", False
            result.refusal_code = decision.code
            result.reasons = decision.reasons
            self.audit.record(intent_id=intent.intent_id, event_type=A.COMMIT_REJECTED,
                              intent_hash=intent.intent_hash, reason=decision.code,
                              metadata={"reasons": decision.reasons})
            self.registry.set_status(intent.intent_id, "BLOCKED", decision.code)
        self._finish(intent, result)
        return result

    # -- effect driver ---------------------------------------------------
    def _drive(self, intent: Intent, effect_id: str, now: float) -> None:
        spec = next(s for s in intent.effects if s.effect_id == effect_id)
        row = self.ledger.get(intent.intent_hash, effect_id)
        if row is None:
            return
        state = row["state"]

        # a duplicate write is only ever issued from PLANNED or NOT_FOUND
        for attempt in range(1, ATTEMPTS_PER_EFFECT + 1):
            row = self.ledger.get(intent.intent_hash, effect_id)
            state = row["state"]
            if state == VERIFIED:
                return
            if state == AWAITING_APPROVAL:
                approval = self.ledger.approval(intent.intent_hash, effect_id)
                if approval is None:
                    return
                self.ledger.transition(intent.intent_hash, effect_id, REQUESTED,
                                       evidence={"approved_by": approval["approved_by"]})
                self.audit.record(intent_id=intent.intent_id, event_type=A.APPROVAL_GRANTED,
                                  intent_hash=intent.intent_hash, new_state=REQUESTED,
                                  reason=f"approved by {approval['approved_by']}")
                continue

            if state in (PLANNED, NOT_FOUND, REQUESTED):
                # A worker taking over an intent must READ before it writes. The worker
                # before it may have landed effects and died without recording them, so
                # this is where a takeover continues from the missing obligations rather
                # than replaying the whole job.
                if state == PLANNED and getattr(self, "_takeover", False):
                    self.ledger.transition(intent.intent_hash, effect_id, RECONCILING)
                    self.audit.record(intent_id=intent.intent_id,
                                      event_type=A.RECONCILIATION_STARTED,
                                      intent_hash=intent.intent_hash, new_state=RECONCILING,
                                      reason="takeover: the previous worker may have acted, "
                                             "so read before writing")
                    verdict = self._bind(intent, spec)
                    self.audit.record(intent_id=intent.intent_id,
                                      event_type=A.BINDING_ATTEMPTED,
                                      intent_hash=intent.intent_hash,
                                      reason=f"takeover: {spec.app}: {verdict.outcome}",
                                      metadata=verdict.as_dict())
                    if verdict.outcome == binding.ONE_MATCH:
                        self.ledger.transition(intent.intent_hash, effect_id, VERIFIED,
                                               external_id=str((verdict.matched or {}).get("id", "")),
                                               evidence={"via": "takeover binding",
                                                         "why": verdict.why})
                        self.audit.record(intent_id=intent.intent_id, event_type=A.BOUND,
                                          intent_hash=intent.intent_hash, new_state=VERIFIED,
                                          reason="the previous worker's effect is already "
                                                 "there; nothing was rewritten")
                        return
                    if verdict.outcome == binding.AMBIGUOUS:
                        self.ledger.transition(intent.intent_hash, effect_id, AMBIGUOUS,
                                               evidence={"candidates": len(verdict.candidates),
                                                         "why": verdict.why})
                        self.audit.record(intent_id=intent.intent_id,
                                          event_type=A.DUPLICATE_DETECTED,
                                          intent_hash=intent.intent_hash, new_state=AMBIGUOUS,
                                          reason=f"takeover: {verdict.why}",
                                          metadata={"candidates": len(verdict.candidates)})
                        return
                    if verdict.outcome == binding.NOT_PROVABLE:
                        self.ledger.transition(intent.intent_hash, effect_id, NOT_PROVABLE,
                                               evidence={"surface": spec.app, "why": verdict.why})
                        self.audit.record(intent_id=intent.intent_id, event_type=A.NOT_PROVABLE,
                                          intent_hash=intent.intent_hash,
                                          new_state=NOT_PROVABLE, reason=verdict.why)
                        return
                    # NOT_FOUND or MISMATCH: nothing of ours is out there, so writing is
                    # the correct next act. Route through NOT_FOUND so the ordinary write
                    # path owns the transition, rather than jumping to a written state.
                    self.ledger.transition(intent.intent_hash, effect_id, NOT_FOUND,
                                           evidence={"binding": verdict.outcome,
                                                     "why": verdict.why})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.BINDING_ATTEMPTED,
                                      intent_hash=intent.intent_hash,
                                      reason=f"takeover: {spec.app} holds nothing of ours; "
                                             "proceeding to write",
                                      metadata=verdict.as_dict())
                    continue
                # An effect that reaches the outside world waits for a named human. The
                # gate sits on the write, so an unapproved outbound effect is never sent;
                # the commit gate then refuses the intent, because a required effect is
                # not verified. Internal effects do not wait for anybody.
                if (state == PLANNED and self._needs_signoff(spec)
                        and self.ledger.approval(intent.intent_hash, effect_id) is None):
                    self.ledger.transition(intent.intent_hash, effect_id, AWAITING_APPROVAL,
                                           evidence={"app": spec.app,
                                                     "why": "outbound effect awaiting a human"})
                    self.audit.record(intent_id=intent.intent_id,
                                      event_type=A.APPROVAL_REQUIRED,
                                      intent_hash=intent.intent_hash,
                                      new_state=AWAITING_APPROVAL,
                                      reason=f"{spec.effect_id} reaches the outside world "
                                             f"through {spec.app}; a named human approves it")
                    return

                decision = policy.can_retry(state) if state == NOT_FOUND else policy.Decision(True)
                if not decision.allow:
                    return
                self.ledger.transition(intent.intent_hash, effect_id, REQUESTED, count_attempt=True)
                self.audit.record(intent_id=intent.intent_id, event_type=A.EFFECT_REQUESTED,
                                  intent_hash=intent.intent_hash, reason=f"{spec.app}.{spec.operation}",
                                  metadata={"effect_id": effect_id, "attempt": attempt})
                try:
                    artifact = self._write(intent, spec)
                    self.ledger.transition(intent.intent_hash, effect_id, EFFECTED,
                                           external_id=str(artifact.get("id", "")),
                                           evidence={"write": "returned"})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.EFFECTED,
                                      intent_hash=intent.intent_hash, new_state=EFFECTED,
                                      metadata={"external_id": artifact.get("id")})
                except TransientError as exc:
                    # attempts counts WRITE attempts, so this does not increment
                    self.ledger.transition(intent.intent_hash, effect_id, UNKNOWN,
                                           evidence={"reason": "no response", "error": str(exc)})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.EFFECT_UNKNOWN,
                                      intent_hash=intent.intent_hash, new_state=UNKNOWN,
                                      reason=str(exc))
                except PermanentError as exc:
                    # The app refused the write outright, so no artifact exists. That
                    # is a rejection, not a failed verification: VERIFICATION_FAILED
                    # means an artifact was found and did not match the contract.
                    self.ledger.transition(intent.intent_hash, effect_id, REJECTED,
                                           evidence={"reason": "app refused the write", "error": str(exc)})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.EFFECT_REJECTED,
                                      intent_hash=intent.intent_hash, new_state=REJECTED,
                                      reason=str(exc))
                    return

            row = self.ledger.get(intent.intent_hash, effect_id)
            state = row["state"]

            # UNKNOWN is reconciled, never retried
            if state == UNKNOWN:
                self.ledger.transition(intent.intent_hash, effect_id, RECONCILING)
                self.audit.record(intent_id=intent.intent_id,
                                  event_type=A.RECONCILIATION_STARTED,
                                  intent_hash=intent.intent_hash, new_state=RECONCILING,
                                  reason="outcome unknown; reading before any retry")
                try:
                    match = self._reconcile(intent, spec)
                except TransientError as exc:
                    # the read that would settle this is itself unavailable.
                    # Stay UNKNOWN: never guess, never retry blind.
                    self.ledger.transition(intent.intent_hash, effect_id, UNKNOWN,
                                           evidence={"reason": "reconciliation read unavailable",
                                                     "error": str(exc)})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.RECONCILIATION_RESOLVED,
                                      intent_hash=intent.intent_hash, reason="read unavailable",
                                      metadata={"error": str(exc)})
                    return
                match = self._reconcile(intent, spec)
                self.ledger.record_reconciliation(
                    intent_hash=intent.intent_hash, effect_id=effect_id, ambiguous_state=UNKNOWN,
                    confidence=match.confidence, found_existing=match.found,
                    strategy=match.strategy, detail=match.detail)
                self.audit.record(intent_id=intent.intent_id,
                                  event_type=A.RECONCILIATION_RESOLVED,
                                  intent_hash=intent.intent_hash, reason=match.confidence,
                                  metadata=match.as_dict())
                if match.confidence == R_AMBIGUOUS:
                    self.ledger.transition(intent.intent_hash, effect_id, AMBIGUOUS)
                    self.audit.record(intent_id=intent.intent_id,
                                      event_type=A.AMBIGUOUS_ESCALATED,
                                      new_state=AMBIGUOUS, reason="a human must choose")
                    return
                if match.found:
                    self.ledger.transition(intent.intent_hash, effect_id, VERIFIED,
                                           external_id=match.object_id,
                                           evidence={"via": "reconciliation", "confidence": match.confidence})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.VERIFIED,
                                      intent_hash=intent.intent_hash, new_state=VERIFIED,
                                      reason=f"reconciled ({match.confidence}); no duplicate written")
                    return
                # Tagged binding found nothing. Before any retry, ask the semantic
                # layer: a crashed writer may never have tagged its object, and a
                # foreign actor never will. Whether "no match" is even a finding
                # depends on the surface, which is the policy in binding.py.
                verdict = self._bind(intent, spec)
                self.audit.record(intent_id=intent.intent_id, event_type=A.BINDING_ATTEMPTED,
                                  intent_hash=intent.intent_hash,
                                  reason=f"{spec.app}: {verdict.outcome}",
                                  metadata=verdict.as_dict())
                if verdict.outcome == binding.ONE_MATCH:
                    self.ledger.transition(intent.intent_hash, effect_id, VERIFIED,
                                           external_id=str((verdict.matched or {}).get("id", "")),
                                           evidence={"via": "semantic binding",
                                                     "why": verdict.why})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.BOUND,
                                      intent_hash=intent.intent_hash, new_state=VERIFIED,
                                      reason="an existing external object carries this "
                                             "intent's meaning; nothing was rewritten")
                    return
                if verdict.outcome == binding.AMBIGUOUS:
                    self.ledger.transition(intent.intent_hash, effect_id, AMBIGUOUS,
                                           evidence={"candidates": len(verdict.candidates),
                                                     "why": verdict.why})
                    self.audit.record(intent_id=intent.intent_id,
                                      event_type=A.DUPLICATE_DETECTED,
                                      intent_hash=intent.intent_hash, new_state=AMBIGUOUS,
                                      reason=verdict.why,
                                      metadata={"candidates": len(verdict.candidates)})
                    return
                if verdict.outcome == binding.NOT_PROVABLE:
                    self.ledger.transition(intent.intent_hash, effect_id, NOT_PROVABLE,
                                           evidence={"surface": spec.app, "why": verdict.why})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.NOT_PROVABLE,
                                      intent_hash=intent.intent_hash, new_state=NOT_PROVABLE,
                                      reason=verdict.why)
                    return
                self.ledger.transition(intent.intent_hash, effect_id, NOT_FOUND,
                                       evidence={"confidence": match.confidence,
                                                 "binding": verdict.outcome})
                continue  # NOT_FOUND is the only retryable state

            # EFFECTED (or a re-verification) -> independent read back
            if state in (EFFECTED, VERIFICATION_FAILED):
                self.ledger.transition(intent.intent_hash, effect_id, VERIFYING)
                self.audit.record(intent_id=intent.intent_id, event_type=A.VERIFICATION_STARTED,
                                  intent_hash=intent.intent_hash, new_state=VERIFYING,
                                  reason="independent read path")
                artifact = None
                try:
                    artifact = self._read_back(intent, spec)
                except TransientError as exc:
                    # the verifier is down. A write succeeded; nothing can be
                    # proven. That is UNKNOWN, not COMMITTED.
                    self.ledger.transition(intent.intent_hash, effect_id, UNKNOWN,
                                           evidence={"reason": "verification read unavailable",
                                                     "error": str(exc)})
                    self.audit.record(intent_id=intent.intent_id, event_type=A.EFFECT_UNKNOWN,
                                      intent_hash=intent.intent_hash, new_state=UNKNOWN,
                                      reason="verification unavailable; refusing to commit")
                    return
                if artifact is None:
                    self.ledger.transition(intent.intent_hash, effect_id, VERIFICATION_FAILED,
                                           evidence={"reason": "object absent on read-back"})
                    self.audit.record(intent_id=intent.intent_id,
                                      event_type=A.VERIFICATION_FAILED,
                                      reason="write reported success but the object is absent")
                    return
                verdict = check(spec.app, artifact, intent=intent, effect=spec)
                if verdict.ok:
                    self.ledger.transition(intent.intent_hash, effect_id, VERIFIED,
                                           external_id=str(artifact.get("id", "")),
                                           evidence=verdict.as_dict())
                    self.audit.record(intent_id=intent.intent_id, event_type=A.VERIFIED,
                                      intent_hash=intent.intent_hash, new_state=VERIFIED,
                                      reason="all postconditions hold",
                                      metadata=verdict.as_dict())
                else:
                    self.ledger.transition(intent.intent_hash, effect_id, VERIFICATION_FAILED,
                                           evidence=verdict.as_dict())
                    self.audit.record(intent_id=intent.intent_id,
                                      event_type=A.VERIFICATION_FAILED,
                                      new_state=VERIFICATION_FAILED,
                                      reason="; ".join(verdict.violations),
                                      metadata=verdict.as_dict())
                return
            return

    # -- app boundary ----------------------------------------------------
    def _write(self, intent: Intent, spec) -> dict:
        """Apply the effect.

        The scope AUTHORIZES; the payload is what the model proposed and what
        actually gets written. Those are different things on purpose: a payload
        that contradicts the frozen scope produces a write that succeeds and an
        effect that fails verification. That is invariant 3 in code, not in prose.
        """
        payload = spec.payload_dict()
        title = payload.get("title") or (
            f"{intent.scope.customer} {intent.scope.project} {intent.scope.event}")
        if spec.app == "calendar":
            start_iso = payload.get("start_iso") or intent.scope.start_iso
            return self.apps.calendar.insert_event(title=title, start_iso=start_iso,
                                                   attendees=intent.scope.recipients,
                                                   intent_hash=intent.intent_hash)
        if spec.app == "linear":
            return self.apps.linear.create_task(project=intent.scope.project, title=title,
                                                intent_hash=intent.intent_hash)
        if spec.app == "slack":
            text = spec.payload_dict().get("text") or (
                f"{intent.scope.customer} {intent.scope.project} {intent.scope.event} — "
                f"{self._human_time(intent.scope.start_iso)} [CAUSAL:{intent.short_hash()}]"
            )
            return self.apps.slack.post_message(channel=spec.target or self._default_channel(intent),
                                                text=text, intent_hash=intent.intent_hash)
        raise PermanentError(f"no writer for app {spec.app!r}")

    def _read_back(self, intent: Intent, spec) -> dict | None:
        """Independent read path. Never receives the write response."""
        if spec.app == "calendar":
            found = self.apps.calendar.list_events(intent_hash=intent.intent_hash)
        elif spec.app == "linear":
            found = self.apps.linear.list_tasks(intent_hash=intent.intent_hash)
        elif spec.app == "slack":
            found = self.apps.slack.list_messages(channel=spec.target or self._default_channel(intent),
                                                  intent_hash=intent.intent_hash)
        else:
            found = []
        return found[-1] if found else None

    def _model_calls(self) -> int:
        """Read the attached model hook's own call count. Not a constant.

        If no hook is attached there is nothing that could have been consulted, so the
        answer is 0 by absence. If one is attached, this is the hook's own counter —
        which is what makes the API's "commits decided by a model" line a measurement
        rather than an assertion.
        """
        return int(getattr(self.planner, "calls", 0) or 0)

    def _needs_signoff(self, spec) -> bool:
        """Outbound effects wait for a human; internal ones do not.

        Authored policy, overridable per engine by an operator who disagrees. Never a
        model's call, and never inferred from the effect's content.
        """
        apps = self.signoff_apps
        return spec.app in (apps if apps is not None else policy.SIGNOFF_REQUIRED_APPS)

    def _fingerprint(self, intent: Intent, spec) -> binding.Fingerprint:
        """The intended effect's meaning, computed from what will actually be written.

        Must mirror _write exactly: if these two disagree, matching fails on objects
        that are genuinely ours.
        """
        payload = spec.payload_dict()
        title = payload.get("title") or (
            f"{intent.scope.customer} {intent.scope.project} {intent.scope.event}")
        if spec.app == "calendar":
            return binding.Fingerprint.of(
                entity=intent.scope.customer, operation=intent.scope.operation or spec.operation,
                resource=intent.scope.resource, title=title,
                start_iso=payload.get("start_iso") or intent.scope.start_iso,
                participants=intent.scope.recipients)
        if spec.app == "slack":
            # the message body carries the project and the human time, not a title
            return binding.Fingerprint.of(
                entity=intent.scope.customer, operation=intent.scope.operation or spec.operation,
                resource=intent.scope.resource or intent.scope.project, title="")
        return binding.Fingerprint.of(
            entity=intent.scope.customer, operation=intent.scope.operation or spec.operation,
            resource=intent.scope.resource or intent.scope.project, title=title)

    def _bind(self, intent: Intent, spec) -> binding.Verdict:
        """Ask the external system what it already holds, and classify it."""
        intended = self._fingerprint(intent, spec)
        app = self.apps.by_name(spec.app)
        search = getattr(app, "search_candidates", None)
        if not callable(search):
            return binding.Verdict(binding.NOT_PROVABLE, spec.app, [], None,
                                   f"{spec.app} exposes no candidate search, so absence "
                                   "cannot be established")
        if spec.app == "calendar":
            candidates = search(start_iso=intended.start_iso, title=intended.title)
        elif spec.app == "slack":
            candidates = search(channel=spec.target or self._default_channel(intent))
        else:
            candidates = search(resource=intended.resource, title=intended.title)
        return binding.classify(surface=spec.app, intended=intended, candidates=candidates,
                                matchability=getattr(self, "matchability_overrides", {}).get(spec.app))

    def post_commit_scan(self, intent: Intent) -> list[dict]:
        """Look again after committing, for equivalents that did not exist when bound.

        CAUSAL will not delete a foreign object and cannot un-commit. What it can do is
        refuse to let a duplicate pass unrecorded.
        """
        findings: list[dict] = []
        for spec in intent.effects:
            row = self.ledger.get(intent.intent_hash, spec.effect_id)
            if row is None or row["state"] != VERIFIED:
                continue
            verdict = self._bind(intent, spec)
            if verdict.outcome != binding.AMBIGUOUS:
                continue
            self.ledger.mark_post_commit_duplicate(
                intent.intent_hash, spec.effect_id,
                evidence={"candidates": len(verdict.candidates), "why": verdict.why})
            self.audit.record(intent_id=intent.intent_id, event_type=A.POST_COMMIT_DUPLICATE,
                              intent_hash=intent.intent_hash, new_state=POST_COMMIT_DUPLICATE,
                              reason=f"{spec.effect_id}: {verdict.why}",
                              metadata={"candidates": len(verdict.candidates)})
            findings.append({"effect_id": spec.effect_id, "app": spec.app,
                             "candidates": len(verdict.candidates)})
        return findings

    def _reconcile(self, intent: Intent, spec):
        if spec.app == "calendar":
            candidates = self.apps.calendar.list_events(intent_hash=intent.intent_hash)
        elif spec.app == "linear":
            candidates = self.apps.linear.list_tasks(intent_hash=intent.intent_hash)
        else:
            candidates = self.apps.slack.list_messages(channel=spec.target or self._default_channel(intent),
                                                       intent_hash=intent.intent_hash)
        # widen the search: same subject, any intent hash. Only possible against
        # in-process state; against real services we rely on the tagged search
        # above, which is the honest limit of what an API will let you query.
        world = getattr(self.apps.calendar, "world", None) or getattr(self.apps.slack, "world", None)
        if not candidates and world is not None:
            if spec.app == "calendar":
                candidates = [e for e in world.events
                              if intent.scope.customer.lower() in str(e.get("title", "")).lower()]
            elif spec.app == "linear":
                candidates = [t for t in world.tasks
                              if t.get("project", "").lower() == intent.scope.project.lower()]
            else:
                candidates = [m for m in world.messages
                              if m.get("channel") == (spec.target or self._default_channel(intent))]
        expected = {"intent_hash": intent.intent_hash, "subject": intent.scope.customer,
                    "operation": intent.scope.event}
        return reconcile(candidates, expected)

    # -- helpers ---------------------------------------------------------
    def _authority_for(self, intent: Intent, fact: str) -> str:
        return intent.authority.get(fact, "gmail")

    def _default_channel(self, intent: Intent) -> str:
        return intent.scope.recipients[0] if intent.scope.recipients else "#engineering"

    @staticmethod
    def _human_time(start_iso: str) -> str:
        from datetime import datetime

        dt = datetime.fromisoformat(start_iso)
        return f"{dt.strftime('%A')} at {dt.strftime('%H:%M')}"

    def _snapshot(self, intent: Intent) -> tuple[list[dict], list[dict]]:
        return self.ledger.all(intent.intent_hash), self.ledger.reconciliations(intent.intent_hash)

    def _refuse(self, result: RunResult, intent: Intent, code: str, reasons: list[str]) -> RunResult:
        result.status, result.refusal_code, result.reasons = "REFUSED", code, reasons
        result.committed = False
        self.audit.record(intent_id=intent.intent_id, event_type=A.INTENT_REFUSED,
                          reason=code, metadata={"reasons": reasons})
        self.registry.set_status(intent.intent_id, "BLOCKED", code)
        self._finish(intent, result)
        return result

    def _finish(self, intent: Intent, result: RunResult) -> None:
        result.audit = self.audit.timeline(intent.intent_id)
        result.model_calls = self._model_calls()
        if self.outcomes is not None:
            self.outcomes.record(intent_id=intent.intent_id, intent_hash=result.intent_hash,
                                 status=result.status, committed=result.committed,
                                 refusal_code=result.refusal_code,
                                 effects=result.effects, model_calls=result.model_calls)
