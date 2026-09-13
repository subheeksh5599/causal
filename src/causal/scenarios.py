"""The sequences, as callable functions.

The CLI harness (`scripts/demo.py`) and the console API (`causal/api.py`) both
drive these, so what a judge clicks is the same code path the tests and the
harness exercise. One implementation, three ways to reach it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import audit as A
from . import binding
from . import plain
from .apps import Apps
from .audit import Audit
from .engine import Causal
from .evidence_sink import OutcomeStore
from .intent import Intent, Scope, conflict_key_for, EffectSpec
from .ledger import EffectLedger
from .registry import Registry

def _fixture_clock() -> tuple[str, str, str, str]:
    """The demo world's dates, computed from a real date rather than frozen.

    The base is the Monday of the current week, so the week's Tuesday is unambiguous and
    the seeded approval and the intent it authorises always agree. None of these is a
    literal: a console opened next year shows next year's dates. The engine takes the
    clock as a parameter (`reference_date_iso`, `now_ts`) and never reaches for the wall
    clock inside a decision, so pinning a run for a reproducible quote is one env var:

        CAUSAL_REFERENCE_DATE=2026-09-14 uv run python scripts/demo.py

    Staleness is measured from each message's own timestamp, not from this date, so
    changing the base cannot make an approval look fresher than it is.
    """
    pinned = os.environ.get("CAUSAL_REFERENCE_DATE", "").strip()
    today = date.fromisoformat(pinned) if pinned else date.today()
    monday = today - timedelta(days=today.weekday())
    tuesday = monday + timedelta(days=1)
    wednesday = monday + timedelta(days=2)
    return (monday.isoformat(), f"{tuesday.isoformat()}T15:00",
            f"{tuesday.isoformat()}T16:00", f"{wednesday.isoformat()}T16:00")


CONTACT = "buyer@acme.example"
REFERENCE_DATE, TUESDAY, TUESDAY_LATE, WEDNESDAY = _fixture_clock()

SEQUENCES = [
    ("intended", "The intended path"),
    ("timeout_after_write", "Timeout AFTER the write — reconciled, never retried blind"),
    ("conflict", "Two intents, one business outcome — the second is refused"),
    ("unauthorized_success", "The API succeeds and the action still fails"),
    ("missing_evidence", "Evidence that does not exist — nothing executes"),
    ("lying_model", "A model claiming total success changes nothing"),
    ("crash_recovery", "Worker A dies holding the job — worker B takes over, no duplicate"),
    ("duplicate_intent", "The same intent arrives twice while the lease is live"),
    ("duplicate_before_commit", "An equivalent effect already exists — refuses to commit"),
    ("duplicate_after_commit", "A duplicate appears after the commit — flagged, not undone"),
    ("absence_not_provable", "Where absence cannot be proven, it never retries"),
    ("counter_intent", "Renew and cancel contend for one contract"),
    ("awaiting_signoff", "Outbound waits for a person; internal effects do not"),
]

ACME_PEOPLE = ("dana.reyes@acme.example", "sam.okafor@acme.example")


def build_binding_intent(customer: str, project: str, start_iso: str, *, intent_id: str,
                         event: str = "Kickoff", operation: str = "", resource: str = "",
                         recipients: tuple[str, ...] = ACME_PEOPLE) -> Intent:
    """An intent that names the real-world resource it contends for.

    `resource`/`operation` are what the authored exclusivity relation is checked
    against; they are deliberately not part of the conflict key.
    """
    scope = Scope(customer=customer, project=project, event=event, start_iso=start_iso,
                  recipients=recipients,
                  allowed_apps=("gmail", "calendar", "linear", "slack"),
                  resource=resource, operation=operation)
    effects = (
        EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT"),
        EffectSpec("LINEAR-01", "linear", "CREATE_ISSUE"),
        EffectSpec("SLACK-01", "slack", "POST_MESSAGE"),
    )
    return Intent(intent_id=intent_id, request=f"{customer} approved the {project}.",
                  scope=scope,
                  authority={"approval": "gmail", "time": "gmail", "meeting": "calendar",
                             "work": "linear"},
                  effects=effects, conflict_key=conflict_key_for(scope))


@dataclass
class Stack:
    apps: Any
    registry: Registry
    ledger: EffectLedger
    audit: Audit
    outcomes: OutcomeStore
    engine: Causal
    mode: str = "LOCAL"
    db: str = ""

    def close(self) -> None:
        for obj in (self.registry, self.ledger, self.audit, self.outcomes):
            try:
                obj.close()
            except Exception:
                pass


def fresh_stack(mode: str = "LOCAL", db_path: str | None = None) -> Stack:
    from .adapters import build_apps

    path = Path(db_path or "./evidence/console.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    apps = Apps.local() if mode.upper() == "LOCAL" else build_apps(mode)
    db = str(path)
    registry, ledger, audit = Registry(db), EffectLedger(db), Audit(db)
    outcomes = OutcomeStore(db)
    engine = Causal(apps, registry, ledger, audit, outcomes,
                    known_contacts={CONTACT}, reference_date_iso=REFERENCE_DATE)
    return Stack(apps, registry, ledger, audit, outcomes, engine, mode.upper(), db)


def build_intent(customer: str, project: str, start_iso: str, *, intent_id: str,
                 event: str = "Kickoff", calendar_payload: str | None = None) -> Intent:
    scope = Scope(customer=customer, project=project, event=event, start_iso=start_iso,
                  recipients=("#engineering",),
                  allowed_apps=("gmail", "calendar", "linear", "slack"))
    effects = (
        EffectSpec("CALENDAR-01", "calendar", "CREATE_EVENT",
                   payload=(("start_iso", calendar_payload),) if calendar_payload else ()),
        EffectSpec("LINEAR-01", "linear", "CREATE_ISSUE"),
    )
    return Intent(intent_id=intent_id, request=f"{customer} approved the {project}.",
                  scope=scope,
                  authority={"approval": "gmail", "time": "gmail", "meeting": "calendar",
                             "work": "linear"},
                  effects=effects, conflict_key=conflict_key_for(scope))


def seed_approval(stack: Stack, customer: str, project: str, start_iso: str,
                  message_id: str) -> str:
    if stack.mode == "LOCAL":
        stack.apps.mail.seed_message(
            message_id, sender=CONTACT,
            body=f"{customer} approved the {project}. Kickoff {start_iso}.", timestamp=None)
    return message_id


def truth(stack: Stack) -> dict:
    out: dict[str, Any] = {}
    for label, client, attr in (("calendar", stack.apps.calendar, "events"),
                                ("linear", stack.apps.linear, "tasks"),
                                ("slack", stack.apps.slack, "messages")):
        world = getattr(client, "world", None)
        out[label] = len(getattr(world, attr)) if world is not None else "live"
    return out


def _delta(before: dict, after: dict) -> dict:
    return {k: (after[k] - before[k]) if isinstance(before.get(k), int) else "live" for k in after}


class LyingPlanner:
    """Claims total success. The engine has no path to consult it.

    The counter is per instance on purpose. As a class attribute it was shared by every
    planner ever constructed, so a count reset in one place was polluted by a call made
    somewhere else — which is a poor property for the number that is supposed to prove
    a negative.
    """

    def __init__(self) -> None:
        self.calls = 0

    def propose(self, *_a, **_k) -> dict:
        self.calls += 1
        return {"status": "COMMITTED", "verified": True, "effects": ["all done"],
                "evidence": "invented"}


def run(name: str, stack: Stack) -> dict:
    """Run one sequence. Returns the report plus the ground truth around it."""
    engine = stack.engine
    # Every sequence starts from the shipped matchability table: a sequence that
    # overrides a surface must not silently re-policy the ones after it.
    engine.matchability_overrides = {}
    before = truth(stack)
    payload: dict[str, Any] = {"name": name, "mode": stack.mode}

    if name == "intended":
        msg = seed_approval(stack, "Acme", "implementation", TUESDAY, "msg-1")
        r = engine.run(build_intent("Acme", "Implementation", TUESDAY, intent_id="C-1042"),
                       evidence_message_id=msg)
        payload |= {"intent_id": "C-1042", "result": r.as_dict(),
                    "narration": "approval → schedule → task → read back → committed"}

    elif name == "timeout_after_write":
        if stack.mode == "LOCAL":
            stack.apps.calendar.fail_after(1)     # it lands; the response never arrives
        msg = seed_approval(stack, "Northwind", "migration", TUESDAY_LATE, "msg-2")
        r = engine.run(build_intent("Northwind", "Migration", TUESDAY_LATE, intent_id="C-1043"),
                       evidence_message_id=msg)
        payload |= {"intent_id": "C-1043", "result": r.as_dict(),
                    "narration": "the write landed, the response was lost: read before acting"}

    elif name == "conflict":
        holder = build_intent("Contoso", "Rollout", TUESDAY, intent_id="C-2001")
        holder.freeze()
        stack.registry.register(intent_id=holder.intent_id, conflict_key=holder.conflict_key,
                                intent_hash=holder.intent_hash)
        msg = seed_approval(stack, "Contoso", "rollout", TUESDAY_LATE, "msg-3")
        r = engine.run(build_intent("Contoso", "Rollout", TUESDAY_LATE, intent_id="C-2002"),
                       evidence_message_id=msg)
        payload |= {"intent_id": "C-2002", "result": r.as_dict(),
                    "holder": holder.intent_id, "conflict_key": holder.conflict_key,
                    "narration": "one business outcome, one active intent"}

    elif name == "unauthorized_success":
        msg = seed_approval(stack, "Fabrikam", "handover", TUESDAY, "msg-4")
        r = engine.run(build_intent("Fabrikam", "Handover", TUESDAY, intent_id="C-3001",
                                    calendar_payload=WEDNESDAY), evidence_message_id=msg)
        payload |= {"intent_id": "C-3001", "result": r.as_dict(),
                    "contradiction": {"authorised": TUESDAY, "written": WEDNESDAY},
                    "narration": "the API accepted it; the outcome is not the authorised one"}

    elif name == "missing_evidence":
        r = engine.run(build_intent("Tailspin", "Audit", TUESDAY, intent_id="C-4001"),
                       evidence_message_id="msg-does-not-exist")
        payload |= {"intent_id": "C-4001", "result": r.as_dict(),
                    "narration": "no approval source → zero writes"}

    elif name == "lying_model":
        planner = LyingPlanner()
        claim = planner.propose()          # what a model would claim, for the panel
        planner.calls = 0                  # reset, so what follows is the engine's doing
        # Attach the hook to the engine so the count is a measurement of an attached
        # model rather than of nothing at all. Nothing in the decision path calls it,
        # which is exactly what `model_calls: 0` then means.
        engine.planner = planner
        r = engine.run(build_intent("Woodgrove", "Rebuild", TUESDAY, intent_id="C-5001"),
                       evidence_message_id="msg-absent")
        engine.planner = None              # never leak a hook into the next sequence
        payload |= {"intent_id": "C-5001", "result": r.as_dict(),
                    "claim": claim, "model_calls": r.model_calls,
                    "planner_attached": True,
                    "narration": "the model says done; the systems disagree"}

    elif name == "crash_recovery":
        msg = seed_approval(stack, "Proseware", "cutover", TUESDAY, "msg-11")
        # Two workers construct the same intent independently. Identical content, so
        # identical hash: one real-world job with two executors.
        a_intent = build_binding_intent("Proseware", "Cutover", TUESDAY, intent_id="C-6001")
        a_intent.freeze()
        title = f"{a_intent.scope.customer} {a_intent.scope.project} {a_intent.scope.event}"
        stack.registry.register(intent_id=a_intent.intent_id, conflict_key=a_intent.conflict_key,
                                intent_hash=a_intent.intent_hash)
        # A's effect lands. It carries no tag from us, which is the case that needs
        # semantic binding rather than a lookup.
        a_event = stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                                    attendees=a_intent.scope.recipients)
        intent = build_binding_intent("Proseware", "Cutover", TUESDAY, intent_id="C-6001")
        # Worker B arrives while A's lease is still live. It must not act at all.
        live = engine.run(intent, evidence_message_id=msg)
        # A reaper notices A is gone.
        stack.registry.expire_lease(intent.intent_id)
        before = truth(stack)      # A's object is not written by B
        resumed = engine.run(intent, evidence_message_id=msg)
        events = stack.apps.calendar.search_candidates(start_iso=TUESDAY, title=title)
        payload |= {"intent_id": intent.intent_id, "result": resumed.as_dict(),
                    "while_lease_live": {"status": live.status, "committed": live.committed,
                                         "writes_attempted": live.writes_attempted,
                                         "reason": (live.reasons or [""])[0]},
                    "a_effect_id": a_event["id"],
                    "events_matching": len(events),
                    "effect_states": {e["effect_id"]: e["state"]
                                      for e in (resumed.effects or [])},
                    "narration": "A's effect is bound, not re-created; B finishes what is missing"}

    elif name == "duplicate_intent":
        msg = seed_approval(stack, "Woodgrove", "handover", TUESDAY, "msg-16")
        first = engine.run(build_binding_intent("Woodgrove", "Handover", TUESDAY,
                                               intent_id="C-B001"), evidence_message_id=msg)
        before = truth(stack)
        # The same real-world request arrives again, from another worker, with its own
        # ticket id but identical meaning.
        second = engine.run(build_binding_intent("Woodgrove", "Handover", TUESDAY,
                                                 intent_id="C-B002"), evidence_message_id=msg)
        payload |= {"intent_id": "C-B002", "result": second.as_dict(),
                    "first_status": first.status,
                    "same_intent_hash": first.intent_hash == second.intent_hash,
                    "narration": "one real-world request, one execution"}

    elif name == "duplicate_before_commit":
        msg = seed_approval(stack, "Fabrikam", "rollout", TUESDAY, "msg-12")
        intent = build_binding_intent("Fabrikam", "Rollout", TUESDAY, intent_id="C-7001")
        title = f"{intent.scope.customer} {intent.scope.project} {intent.scope.event}"
        # An outside actor already created an equivalent object, untagged.
        stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                          attendees=intent.scope.recipients)
        before = truth(stack)      # the foreign object is not written by this run
        r = engine.run(intent, evidence_message_id=msg)
        payload |= {"intent_id": intent.intent_id, "result": r.as_dict(),
                    "candidates": len(stack.apps.calendar.search_candidates(
                        start_iso=TUESDAY, title=title)),
                    "effect_states": {e["effect_id"]: e["state"] for e in (r.effects or [])},
                    "narration": "the write succeeded and the intent is refused anyway"}

    elif name == "duplicate_after_commit":
        msg = seed_approval(stack, "Adventure", "renewal", TUESDAY, "msg-13")
        intent = build_binding_intent("Adventure", "Renewal", TUESDAY, intent_id="C-8001")
        title = f"{intent.scope.customer} {intent.scope.project} {intent.scope.event}"
        committed = engine.run(intent, evidence_message_id=msg)
        # It was clean when we committed. Then something outside created an equivalent.
        stack.apps.calendar.foreign_event(title=title, start_iso=TUESDAY,
                                          attendees=intent.scope.recipients)
        before = truth(stack)      # nor is this one; only the scan is ours here
        findings = engine.post_commit_scan(intent)
        payload |= {"intent_id": intent.intent_id, "result": committed.as_dict(),
                    "post_commit_findings": findings,
                    "effect_states": {e["effect_id"]: e["state"]
                                      for e in stack.ledger.all(intent.intent_hash)},
                    "narration": "committed, then a duplicate appears: flagged, not undone"}

    elif name == "absence_not_provable":
        # The operator marks Slack as a surface whose read cannot prove absence. The
        # write never lands, and the honest answer is not "it isn't there".
        stack.engine.matchability_overrides = {"slack": "BEST_EFFORT"}
        stack.apps.slack.fail_before(1)
        msg = seed_approval(stack, "Litware", "zenith", TUESDAY, "msg-14")
        intent = build_binding_intent("Litware", "Zenith", TUESDAY, intent_id="C-9001")
        r = engine.run(intent, evidence_message_id=msg)
        payload |= {"intent_id": intent.intent_id, "result": r.as_dict(),
                    "slack_messages_matching": len(stack.apps.slack.search_candidates(
                        channel=engine._default_channel(intent))),
                    "effect_states": {e["effect_id"]: e["state"] for e in (r.effects or [])},
                    "narration": "absence could not be established, so nothing was retried"}

    elif name == "counter_intent":
        holder = build_binding_intent("Contoso", "Renewal", TUESDAY, intent_id="C-A001",
                                      event="Renewal", operation="renew",
                                      resource="CONTRACT-482")
        holder.freeze()
        stack.registry.register(intent_id=holder.intent_id, conflict_key=holder.conflict_key,
                                intent_hash=holder.intent_hash)
        stack.registry.note_scope_identity(holder.intent_id, resource="CONTRACT-482",
                                           operation="renew")
        msg = seed_approval(stack, "Contoso", "cancellation", TUESDAY, "msg-15")
        cancel = build_binding_intent("Contoso", "Cancellation", TUESDAY, intent_id="C-A002",
                                      event="Cancellation", operation="cancel_renew",
                                      resource="CONTRACT-482")
        r = engine.run(cancel, evidence_message_id=msg)
        payload |= {"intent_id": cancel.intent_id, "result": r.as_dict(),
                    "holder": holder.intent_id,
                    "different_conflict_keys": holder.conflict_key != cancel.conflict_key,
                    "policy_digest": binding.EXCLUSIVITY_DIGEST,
                    "narration": "two operations that must never both be live"}

    elif name == "awaiting_signoff":
        # The operator declares which surfaces reach the outside world. Slack does;
        # everything else runs without asking anybody. Pressing Approve on the review
        # page records the approval, and this same sequence resumes on the next press.
        stack.engine.signoff_apps = {"slack"}
        msg = seed_approval(stack, "Margie", "renewal", TUESDAY, "msg-17")
        intent = build_binding_intent("Margie", "Renewal", TUESDAY, intent_id="C-C001")
        r = engine.run(intent, evidence_message_id=msg)
        stack.engine.signoff_apps = None          # never leak an operator policy
        effects = stack.ledger.all(intent.intent_hash)
        approvals = stack.ledger.approvals(intent.intent_hash)
        payload |= {
            "intent_id": intent.intent_id, "result": r.as_dict(),
            "effect_states": {e["effect_id"]: e["state"] for e in effects},
            "awaiting": [e["effect_id"] for e in effects
                         if e["state"] == "AWAITING_APPROVAL"],
            "approved_by": [a["approved_by"] for a in approvals],
            "plain": plain.job_view(
                intent_id=intent.intent_id,
                status=(stack.registry.get(intent.intent_id) or {}).get("status", r.status),
                refusal_code=r.refusal_code, effects=effects, approvals=approvals,
                committed=r.committed),
            "narration": "what a person approving this outcome would read",
        }

    else:
        raise ValueError(f"unknown sequence {name!r}")
    payload["wrote"] = _delta(before, truth(stack))
    payload["truth"] = truth(stack)
    payload["audit"] = stack.audit.timeline(payload["intent_id"])
    # two distinct claims, reported separately rather than blurred into one "ok"
    payload["audit_intact"] = stack.audit.verify_chain(payload["intent_id"])
    payload["chain_linked"] = stack.audit.verify_chain()
    return payload


def run_all(stack: Stack) -> list[dict]:
    return [run(name, stack) for name, _ in SEQUENCES]
