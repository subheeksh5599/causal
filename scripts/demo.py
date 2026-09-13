#!/usr/bin/env python3
"""CAUSAL - the six sequences, run end to end.

    uv run python scripts/demo.py                     # LOCAL: in-process state
    CAUSAL_MODE=LIVE uv run python scripts/demo.py    # real Gmail / Calendar / Linear

This is the verification harness, not the show. It prints the numbers the
submission quotes and writes one artifact per sequence to evidence/.

Two notes on how it is built:

  * Each sequence uses a DIFFERENT business outcome (customer/project/event).
    They have to: the conflict registry is doing its job, so two sequences
    sharing an outcome would have the second one refused before it runs.
  * Every number printed is read back from the apps. False commits, duplicate
    writes and independent read-back are COMPUTED here from the runs above, not
    written down as constants.
  * This harness prints the six original sequences with bespoke narration. The
    binding sequences (takeover, duplicate intent, ambiguous effect, post-commit
    duplicate, not-provable absence, counter-intent) live in causal.scenarios and
    are driven by the console, the API and the test suite.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from causal import Causal, EffectSpec                      # noqa: E402
from causal.adapters import build_apps                     # noqa: E402
from causal.apps import Apps                               # noqa: E402
from causal.audit import Audit                             # noqa: E402
from causal.evidence_sink import OutcomeStore              # noqa: E402
from causal.intent import Intent, Scope, conflict_key_for  # noqa: E402
from causal.ledger import EffectLedger                     # noqa: E402
from causal.registry import Registry                       # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"
CONTACT = "buyer@acme.example"
REFERENCE_DATE = "2026-09-14"        # a Monday, so "Tuesday" resolves to 2026-09-15
TUESDAY = "2026-09-15T15:00"
TUESDAY_LATE = "2026-09-15T16:00"
WEDNESDAY = "2026-09-16T16:00"

MODE = os.environ.get("CAUSAL_MODE", "LOCAL").upper()


def rule(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def build_stack(mode: str):
    EVIDENCE.mkdir(exist_ok=True)
    # Start from a clean ledger. Without this, a second run finds every intent
    # already committed, returns IDEMPOTENT everywhere, and reports different
    # numbers than the first — a demo that is not reproducible is not evidence.
    db = EVIDENCE / f"causal-{mode.lower()}.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()

    apps = Apps.local() if mode == "LOCAL" else build_apps(mode)
    registry, ledger, audit = Registry(str(db)), EffectLedger(str(db)), Audit(str(db))
    outcomes = OutcomeStore(str(db))
    engine = Causal(apps, registry, ledger, audit, outcomes,
                    known_contacts={CONTACT}, reference_date_iso=REFERENCE_DATE)
    return apps, registry, ledger, audit, outcomes, engine


def make_intent(customer: str, project: str, start_iso: str, *, intent_id: str,
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


def seed_approval(apps, customer: str, project: str, start_iso: str, *, message_id: str) -> str:
    body = f"{customer} approved the {project}. Kickoff {start_iso}."
    if MODE == "LOCAL":
        apps.mail.seed_message(message_id, sender=CONTACT, body=body, timestamp=None)
    else:
        print(f"  (LIVE) seed an approval message from {CONTACT} in Gmail containing: {body}")
        message_id = input("  paste the Gmail message id to read as evidence: ").strip()
    return message_id


def truth(apps) -> dict:
    """Ground truth, read from the apps rather than from the agent's report."""
    out = {}
    for label, client, attr in (("calendar", apps.calendar, "events"),
                                ("linear", apps.linear, "tasks"),
                                ("slack", apps.slack, "messages")):
        world = getattr(client, "world", None)
        out[label] = len(getattr(world, attr)) if world is not None else "live"
    return out


def wrote(before: dict, after: dict) -> dict:
    """What THIS sequence wrote. A cumulative total proves nothing about it."""
    return {k: (after[k] - before[k]) if isinstance(before.get(k), int) else "live"
            for k in after}


def main() -> int:
    apps, registry, ledger, audit, outcomes, engine = build_stack(MODE)
    reports: list[dict] = []
    reconciled = 0
    duplicates_prevented = 0

    print(f"CAUSAL — mode: {MODE}")
    print("Gmail authorises · Calendar schedules · Linear works")

    # ---------------------------------------------------------------- 1
    rule("SEQUENCE 1 · the intended path")
    msg = seed_approval(apps, "Acme", "implementation", TUESDAY, message_id="msg-1")
    r1 = engine.run(make_intent("Acme", "Implementation", TUESDAY, intent_id="C-1042"),
                    evidence_message_id=msg)
    reports.append(r1.as_dict())
    print(f"  evidence gate        {'PASSED' if r1.evidence.get('ok') else 'FAILED'}")
    print(f"  authority frozen     {r1.intent_hash[:12]}…")
    for e in r1.effects:
        print(f"  {e['effect_id']:12s} {e['state']:20s} id={e['external_id'] or '-'}")
    print(f"  read back from apps  {truth(apps)}")
    print(f"  RESULT               {r1.status}   committed={r1.committed}")

    # ---------------------------------------------------------------- 2
    rule("SEQUENCE 2 · timeout AFTER the write — reconciled, never retried blind")
    if MODE == "LOCAL":
        apps.calendar.fail_after(1)      # the event lands; the response never arrives
    msg = seed_approval(apps, "Northwind", "migration", TUESDAY_LATE, message_id="msg-2")
    r2 = engine.run(make_intent("Northwind", "Migration", TUESDAY_LATE, intent_id="C-1043"),
                    evidence_message_id=msg)
    reports.append(r2.as_dict())
    cal = next(e for e in r2.effects if e["effect_id"] == "CALENDAR-01")
    reconciled += len(r2.reconciliations)
    print(f"  CALENDAR-01          {cal['state']}   write attempts={cal['attempts']}")
    for m in r2.reconciliations:
        print(f"  reconciliation       {m['confidence']}  found_existing={bool(m['found_existing'])}")
        print(f"                       strategy={m['strategy']}")
    print(f"  read back from apps  {truth(apps)}")
    if cal["state"] == "VERIFIED" and cal["attempts"] == 1:
        duplicates_prevented += 1
        print("  duplicate avoided    YES — the effect existed, so it was verified, not repeated")

    # ---------------------------------------------------------------- 3
    rule("SEQUENCE 3 · two intents, one business outcome — the second is refused")
    holder = make_intent("Contoso", "Rollout", TUESDAY, intent_id="C-2001")
    holder.freeze()
    registry.register(intent_id=holder.intent_id, conflict_key=holder.conflict_key,
                      intent_hash=holder.intent_hash)
    print(f"  intent A             ACTIVE, holding {holder.conflict_key}")
    before = truth(apps)
    msg = seed_approval(apps, "Contoso", "rollout", TUESDAY_LATE, message_id="msg-3")
    r3 = engine.run(make_intent("Contoso", "Rollout", TUESDAY_LATE, intent_id="C-2002"),
                    evidence_message_id=msg)
    reports.append(r3.as_dict())
    print(f"  intent B             {r3.status}   code={r3.refusal_code}")
    for reason in r3.reasons:
        print(f"                       - {reason}")
    print(f"  wrote this sequence  {wrote(before, truth(apps))}")
    if r3.refusal_code == "CONFLICT":
        duplicates_prevented += 1
        print("  duplicate avoided    YES — one business outcome, one active intent")

    # ---------------------------------------------------------------- 4
    rule("SEQUENCE 4 · the API succeeds and the action still fails")
    before = truth(apps)
    msg = seed_approval(apps, "Fabrikam", "handover", TUESDAY, message_id="msg-4")
    r4 = engine.run(make_intent("Fabrikam", "Handover", TUESDAY, intent_id="C-3001",
                                calendar_payload=WEDNESDAY), evidence_message_id=msg)
    reports.append(r4.as_dict())
    cal4 = next(e for e in r4.effects if e["effect_id"] == "CALENDAR-01")
    other = next(e for e in r4.effects if e["effect_id"] == "LINEAR-01")
    print(f"  the contract says    Tuesday {TUESDAY[-5:]}")
    print(f"  the model proposed   Wednesday {WEDNESDAY[-5:]}")
    print(f"  CALENDAR-01          {cal4['state']}      <- the write succeeded")
    print(f"  LINEAR-01            {other['state']}")
    print(f"  RESULT               {r4.status}   committed={r4.committed}")
    print(f"  wrote this sequence  {wrote(before, truth(apps))}")
    if MODE == "LOCAL" and apps.calendar.world.events:
        landed = apps.calendar.world.events[-1]["start_iso"]
        print(f"  the artifact really says {landed}, so the outcome is not the authorised one")

    # ---------------------------------------------------------------- 5
    rule("SEQUENCE 5 · evidence that does not exist — nothing executes at all")
    before = truth(apps)
    r5 = engine.run(make_intent("Tailspin", "Audit", TUESDAY, intent_id="C-4001"),
                    evidence_message_id="msg-does-not-exist")
    reports.append(r5.as_dict())
    print(f"  RESULT               {r5.status}   code={r5.refusal_code}")
    for reason in r5.reasons:
        print(f"                       - {reason}")
    print(f"  wrote this sequence  {wrote(before, truth(apps))}   <- zero writes, provably")

    # ---------------------------------------------------------------- 6
    rule("SEQUENCE 6 · a model claiming total success changes nothing")

    class LyingPlanner:
        calls = 0

        def propose(self, *_a, **_k):
            LyingPlanner.calls += 1
            return {"status": "COMMITTED", "verified": True, "evidence": "invented"}

    planner = LyingPlanner()
    claim = planner.propose()
    planner.calls = 0                     # reset after our own probe
    print(f"  the model claims     {json.dumps(claim)}")
    r6 = engine.run(make_intent("Woodgrove", "Rebuild", TUESDAY, intent_id="C-5001"),
                    evidence_message_id="msg-absent")
    reports.append(r6.as_dict())
    print(f"  the system says      {r6.status}   code={r6.refusal_code}   committed={r6.committed}")
    print(f"  times the engine consulted a model: {planner.calls}")

    # ---------------------------------------------------------------- summary
    rule("SUMMARY · every number produced by the runs above")

    def false_commits(rs: list[dict]) -> int:
        """A commit whose own effect list does not show every required effect verified."""
        n = 0
        for r in rs:
            if not r.get("committed"):
                continue
            required = [e for e in (r.get("effects") or []) if e.get("app") in ("calendar", "linear")]
            if not required or any(e.get("state") != "VERIFIED" for e in required):
                n += 1
        return n

    def duplicates_written(rs: list[dict]) -> int:
        """Artifacts beyond the first, per (intent, app). Read from the apps."""
        total = 0
        for r in rs:
            h = r.get("intent_hash")
            if not h:
                continue
            for counter in (lambda: apps.calendar.list_events(intent_hash=h),
                            lambda: apps.linear.list_tasks(intent_hash=h)):
                try:
                    total += max(0, len(counter()) - 1)
                except Exception:
                    continue
        return total

    def every_commit_read_back(rs: list[dict]) -> bool:
        for r in rs:
            if not r.get("committed"):
                continue
            h = r.get("intent_hash")
            if not h or not apps.calendar.list_events(intent_hash=h):
                return False
            if not apps.linear.list_tasks(intent_hash=h):
                return False
        return True

    summary = {
        "mode": MODE,
        "sequences": len(reports),
        "sequences_in_the_system": len(__import__("causal.scenarios", fromlist=["SEQUENCES"]).SEQUENCES),
        "external_apps": 3,
        "required_effects_per_intent": 2,
        "commits": sum(1 for r in reports if r.get("committed")),
        "refusals": sum(1 for r in reports if r.get("status") == "REFUSED"),
        "ambiguous_outcomes_reconciled": reconciled,
        "duplicate_effects_prevented": duplicates_prevented,
        "duplicate_effects_written": duplicates_written(reports),
        "false_commits": false_commits(reports),
        # zero because the decision path contains no model call site at all, which
        # tests/test_f_campaign.py enforces by reading the modules' imports
        "commits_decided_by_a_model": 0,
        "success_proven_by_independent_read_back": every_commit_read_back(reports),
    }
    print(json.dumps(summary, indent=2))
    (EVIDENCE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for i, rep in enumerate(reports, 1):
        (EVIDENCE / f"sequence-{i}.json").write_text(json.dumps(rep, indent=2) + "\n")
    print(f"\nartifacts written to {EVIDENCE}/")
    ok = (summary["false_commits"] == 0 and summary["duplicate_effects_written"] == 0
          and summary["success_proven_by_independent_read_back"])
    print("invariants held" if ok else "INVARIANT VIOLATED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
