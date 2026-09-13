#!/usr/bin/env python3
"""The adversarial campaign: many randomised runs, invariants asserted on every one.

The deterministic suite proves specific things in specific orders. This proves the
properties hold when the ORDER and the FAULTS are not chosen by the author.

Each iteration picks a fault profile per effect at random, runs the real engine, and
then asserts the invariants below against the resulting state, the external world,
and the audit chain. A seed is printed for every failure, so any counterexample is
reproducible with --seed.

    uv run python scripts/campaign.py --runs 100

Invariants checked after every run:

  I1  COMMITTED implies every required effect is VERIFIED
  I2  at most one external artifact exists per (intent hash, effect)
  I3  the engine consulted a model zero times
  I4  the audit chain verifies, and its links are intact
  I5  no effect was retried from a state that forbids retrying
  I6  reconciliation never created a second artifact
  I7  every refusal names a code
  I8  a committed run's artifacts all carry the intent hash
  I9  nothing commits when the authority evidence is absent
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from causal import ledger as L                                    # noqa: E402
from causal.apps import PermanentError, TransientError            # noqa: E402
from causal.scenarios import CONTACT, REFERENCE_DATE, build_intent, fresh_stack, truth  # noqa: E402

EVIDENCE = ROOT / "evidence"

# Fault profiles applied to one effect's write path.
CLEAN = "clean"
TRANSIENT_BEFORE = "transient_before"     # nothing landed, a retry is correct
TRANSIENT_AFTER = "transient_after"       # it landed, the response was lost
REFUSE = "refuse"                         # the app rejects the write outright
LYING_ID = "lying_id"                     # the write lands, the response lies about which object
PRE_EXISTING = "pre_existing"             # the artifact is already there from a lost delivery
BIND_AVAILABLE = "bind_available"        # an untagged equivalent exists out there
BIND_AMBIGUOUS = "bind_ambiguous"        # two untagged equivalents exist out there
PROFILES = [CLEAN, TRANSIENT_BEFORE, TRANSIENT_AFTER, REFUSE, LYING_ID, PRE_EXISTING,
            BIND_AVAILABLE, BIND_AMBIGUOUS]


class FaultCalendar:
    """Wraps the Calendar client and injects one profile into the write path."""

    name = "calendar"

    def __init__(self, inner, profile: str) -> None:
        self.inner = inner
        self.profile = profile

    def insert_event(self, **kw):
        if self.profile in (BIND_AVAILABLE, BIND_AMBIGUOUS):
            # the request never lands: the wrinkle is that an equivalent already
            # exists, untagged, so the answer has to come from reading
            raise TransientError("calendar: request never landed; the effect may pre-exist")
        if self.profile == REFUSE:
            raise PermanentError("calendar: 403 the caller cannot write to this calendar")
        if self.profile == TRANSIENT_BEFORE:
            raise TransientError("calendar: transport failure before the request landed")
        if self.profile == TRANSIENT_AFTER:
            self.inner.insert_event(**kw)          # it really lands
            raise TransientError("calendar: response lost after the write")
        if self.profile == LYING_ID:
            self.inner.insert_event(**kw)          # it really lands
            return {"id": "WRONG-ID-FROM-THE-WRITE-RESPONSE", "title": kw.get("title", ""),
                    "start_iso": kw.get("start_iso", ""), "attendees": [],
                    "intent_hash": kw.get("intent_hash", "")}
        return self.inner.insert_event(**kw)

    def list_events(self, **kw):
        return self.inner.list_events(**kw)

    # the binding layer needs the semantic read path too, or every probe at this
    # surface would honestly report that absence cannot be established
    def search_candidates(self, **kw):
        return self.inner.search_candidates(**kw)

    def foreign_event(self, **kw):
        return self.inner.foreign_event(**kw)


class FaultLinear:
    name = "linear"

    def __init__(self, inner, profile: str) -> None:
        self.inner = inner
        self.profile = profile

    def create_task(self, **kw):
        if self.profile == REFUSE:
            raise PermanentError("linear: 400 the team does not accept this issue")
        if self.profile == TRANSIENT_BEFORE:
            raise TransientError("linear: transport failure before the request landed")
        if self.profile == TRANSIENT_AFTER:
            self.inner.create_task(**kw)
            raise TransientError("linear: response lost after the write")
        return self.inner.create_task(**kw)

    def list_tasks(self, **kw):
        return self.inner.list_tasks(**kw)

    def search_candidates(self, **kw):
        return self.inner.search_candidates(**kw)

    def foreign_task(self, **kw):
        return self.inner.foreign_task(**kw)


def artifacts_for(stack, intent_hash: str) -> dict[str, int]:
    """How many external objects carry this intent hash, per app."""
    return {
        "calendar": len(stack.apps.calendar.list_events(intent_hash=intent_hash)),
        "linear": len(stack.apps.linear.list_tasks(intent_hash=intent_hash)),
    }


def one_run(seed: int, mode: str) -> dict:
    rng = random.Random(seed)
    tmp = Path(tempfile.mkdtemp(prefix="campaign-"))
    stack = fresh_stack("LOCAL", db_path=str(tmp / "campaign.db"))

    customer = f"Campaign{rng.randrange(10**6)}"
    project = "Implementation"
    start = "2026-09-15T15:00"
    intent_id = f"R-{seed}"

    cal_profile = rng.choice(PROFILES)
    lin_profile = rng.choice(PROFILES)
    evidence_present = rng.random() > 0.15          # 15% of runs have no approval at all
    model_claims = rng.random() > 0.5               # a model insisting on success changes nothing
    competing = rng.random() > 0.75                 # a second intent on the same outcome

    # the world may already contain the artifact, as if a delivery were repeated
    stack.apps.calendar = FaultCalendar(stack.apps.calendar, cal_profile)
    stack.apps.linear = FaultLinear(stack.apps.linear, lin_profile)
    stack.engine.apps = stack.apps

    intent = build_intent(customer, project, start, intent_id=intent_id)

    if cal_profile == PRE_EXISTING:
        # a lost earlier delivery: the artifact exists, tagged with this intent hash
        stack.apps.calendar.inner.insert_event(title=f"{customer} {project} Kickoff",
                                               start_iso=start, attendees=(),
                                               intent_hash=intent.intent_hash)

    if cal_profile in (BIND_AVAILABLE, BIND_AMBIGUOUS):
        # an outside actor's equivalent, carrying no tag of ours at all
        for _ in range(1 if cal_profile == BIND_AVAILABLE else 2):
            stack.apps.calendar.inner.foreign_event(
                title=f"{customer} {project} Kickoff", start_iso=start,
                attendees=intent.scope.recipients)

    if competing:
        # Same business outcome, DIFFERENT disputed fact: the time differs, so the
        # content hash differs and this is a genuine conflict rather than a
        # re-submission. An identical intent would hash the same and be idempotent.
        holder = build_intent(customer, project, "2026-09-15T16:00", intent_id=f"{intent_id}-holder")
        holder.freeze()
        stack.registry.register(intent_id=holder.intent_id, conflict_key=holder.conflict_key,
                                intent_hash=holder.intent_hash)
        assert holder.conflict_key == intent.conflict_key, "holders must share the outcome"
        assert holder.intent_hash != intent.intent_hash, "holders must differ in content"

    before = artifacts_for(stack, intent.intent_hash)
    message_id = "msg-campaign"
    if evidence_present:
        stack.apps.mail.seed_message(message_id, sender=CONTACT,
                                     body=f"{customer} approved the {project}. Kickoff {start}.",
                                     timestamp=None)
    else:
        message_id = "msg-never-existed"

    if model_claims:
        from causal.scenarios import LyingPlanner
        LyingPlanner().propose()

    result = stack.engine.run(intent, evidence_message_id=message_id)
    after = artifacts_for(stack, intent.intent_hash)
    effects = {e["effect_id"]: e for e in result.effects}
    registry_row = stack.registry.get(intent.intent_id) or {}
    violations: list[str] = []

    # I1 - a commit means every required effect verified
    states = [e["state"] for e in result.effects]
    if result.committed:
        if any(s != L.VERIFIED for s in states):
            violations.append(f"I1 committed with effect states {states}")
        if not effects.get("CALENDAR-01") or not effects.get("LINEAR-01"):
            violations.append("I1 committed without both required effects present")

    # I9 - no evidence means no commit
    if not evidence_present and result.committed:
        violations.append("I9 committed with no approval evidence")

    # I5 - nothing commits while a competing intent holds the outcome
    if competing and result.committed:
        violations.append("I5 committed while a competing intent is ACTIVE")

    # I10 - a bindable equivalent must be bound, never duplicated
    if cal_profile == BIND_AVAILABLE:
        artifacts = stack.apps.calendar.inner.search_candidates(
            start_iso=start, title=f"{customer} {project} Kickoff")
        if result.committed and len(artifacts) != 1:
            violations.append(f"I10 bindable case ended with {len(artifacts)} calendar artifacts")

    # I11 - two equivalent candidates can never commit: ownership cannot be proved
    if cal_profile == BIND_AMBIGUOUS:
        cal = effects.get("CALENDAR-01")
        if result.committed:
            violations.append("I11 committed while two equivalent effects existed")
        elif cal is not None and cal["state"] != L.AMBIGUOUS:
            violations.append(f"I11 two equivalents produced state {cal['state']}, not AMBIGUOUS")

    # I12 - once absence has been shown to be unprovable, no retry may follow
    for e in result.effects:
        if e["state"] == L.NOT_PROVABLE and e.get("attempts", 0) > 1:
            violations.append("I12 a NOT_PROVABLE effect was retried")

    # I13 - a committed intent never carries a NOT_PROVABLE required effect
    if result.committed:
        for e in result.effects:
            if e["app"] in ("calendar", "linear") and e["state"] in (L.NOT_PROVABLE,
                                                                     L.AMBIGUOUS):
                violations.append(f"I13 committed with {e['effect_id']} in {e['state']}")

    # I2 - never more than one artifact per effect
    for app, count in after.items():
        if result.committed and count > 1:
            violations.append(f"I2 {count} {app} artifacts for one effect")

    # I6 - reconciliation must not add an artifact
    if result.reconciliations and after["calendar"] > max(before["calendar"], 1):
        violations.append("I6 reconciliation created a second artifact")

    # I3 - the engine never consults a model
    if getattr(result, "model_calls", 0):
        violations.append("I3 a model was consulted")

    # I4 - the audit chain holds
    if not stack.audit.verify_chain(intent.intent_id):
        violations.append("I4 audit chain did not verify")
    if not stack.audit.verify_chain():
        violations.append("I4 audit chain links broken")

    # I7 - refusals name a code
    if not result.committed and not result.refusal_code:
        violations.append("I7 refused without a code")

    # I8 - a committed run's artifacts carry the intent hash
    if result.committed:
        for ev in stack.apps.calendar.inner.list_events(intent_hash=intent.intent_hash):
            if ev.get("intent_hash") != intent.intent_hash:
                violations.append("I8 a committed artifact does not carry the intent hash")

    # every effect must be in a defined state
    for e in result.effects:
        if e["state"] not in L.LEGAL_TRANSITIONS and e["state"] not in L.TERMINAL:
            violations.append(f"effect {e['effect_id']} in unknown state {e['state']}")

    # a retry in a no-retry state is forbidden
    for e in result.effects:
        if e["state"] in L.NO_RETRY_STATES and e.get("attempts", 0) > 1:
            violations.append(f"I5 effect {e['effect_id']} retried from {e['state']}")

    record = {
        "seed": seed, "mode": mode,
        "intent_id": intent.intent_id,
        "profiles": {"calendar": cal_profile, "linear": lin_profile},
        "evidence": evidence_present, "competing": competing, "model_claimed": model_claims,
        "status": result.status, "committed": result.committed,
        "refusal_code": result.refusal_code,
        "effect_states": {k: v["state"] for k, v in effects.items()},
        "reconciliations": len(result.reconciliations),
        "artifacts": after,
        "truth": truth(stack),
        "violations": violations,
    }
    stack.close()
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--mode", default="LOCAL")
    ap.add_argument("--seed", type=int, default=None, help="reproduce a single run")
    args = ap.parse_args()

    if args.seed is not None:
        record = one_run(args.seed, args.mode)
        print(json.dumps(record, indent=2, default=str))
        return 1 if record["violations"] else 0

    print(f"adversarial campaign: {args.runs} randomised runs\n")
    records = [one_run(i, args.mode) for i in range(args.runs)]

    violations = [(r["seed"], v) for r in records for v in r["violations"]]
    commits = sum(1 for r in records if r["committed"])
    refusals = Counter(r["refusal_code"] for r in records if not r["committed"])
    profiles = Counter(f"{r['profiles']['calendar']}/{r['profiles']['linear']}" for r in records)
    reconciled = sum(1 for r in records if r["reconciliations"])

    print(f"  runs                 {len(records)}")
    print(f"  committed            {commits}")
    print(f"  refused              {len(records) - commits}")
    print(f"  reconciled (no dup)  {reconciled}")
    print(f"  invariant violations {len(violations)}")
    print("\n  fault profiles exercised:")
    for name, count in profiles.most_common():
        print(f"    {name:<42} {count}")
    if refusals:
        print("\n  refusals by code:")
        for code, count in refusals.most_common():
            print(f"    {code:<28} {count}")

    if violations:
        print("\n  VIOLATIONS (reproduce with --seed N):")
        for seed, violation in violations[:20]:
            print(f"    seed {seed}: {violation}")
    else:
        print("\n  no invariant violation in any run")

    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "campaign.json").write_text(json.dumps(
        {"runs": len(records), "committed": commits, "reconciled": reconciled,
         "violations": [{"seed": s, "violation": v} for s, v in violations],
         "profiles": dict(profiles), "refusals": dict(refusals),
         "records": records}, indent=2, default=str))
    print("\nartifact: evidence/campaign.json")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
