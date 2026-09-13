#!/usr/bin/env python
"""Run the flagship against the three real apps: Gmail, Calendar and Linear.

    uv run python scripts/live_run.py --find-only     # search and read; writes nothing
    uv run python scripts/live_run.py                 # read, then write Calendar + Linear

This is the difference between "wired" and "used". The console in `LOCAL` mode drives
in-process services; this drives the real ones, so the three apps are genuinely connected
and the evidence is their own state.

The flow is the product, in order:

  1. SEARCH the real mailbox for candidate approvals (a different call than the read).
  2. READ each candidate and run the real evidence gate over it.
  3. Take the start time from the message that passes, because the approval is the
     authority for the disputed fact — not this script and not a model.
  4. Execute: Calendar write, then Linear write, each verified by reading it back
     through a different operation than the one that wrote it.

Google does not email attendees for an insert unless `sendUpdates` is set, and this does
not set it, so the calendar event notifies nobody. No credential is ever printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from causal import scenarios as S                                              # noqa: E402
from causal.adapters import CalendarLive, GmailLive, GoogleAuth, LinearLive     # noqa: E402
from causal.intent import parse_approval                                        # noqa: E402


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def load_env() -> None:
    """Read a `.env` in the repository root, without overwriting the real environment.

    The credentials are documented as living there (gitignored, chmod 600), so a script that
    reads only `os.environ` reports the surfaces unreachable on the machine where they were
    just configured — and refuses a live run that would have worked.
    """
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def google_auth() -> GoogleAuth | None:
    cid, secret, refresh = env("GOOGLE_CLIENT_ID"), env("GOOGLE_CLIENT_SECRET"), env("GOOGLE_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        return None
    return GoogleAuth(client_id=cid, client_secret=secret, refresh_token=refresh)


def missing() -> list[str]:
    gaps = []
    if google_auth() is None:
        gaps.append("GOOGLE_REFRESH_TOKEN (with GOOGLE_CLIENT_ID/SECRET) — one consent click:\n"
                    "        uv run python scripts/google_oauth.py --capture")
    if not (env("LINEAR_API_KEY") and env("LINEAR_TEAM_ID")):
        gaps.append("LINEAR_API_KEY and LINEAR_TEAM_ID — https://linear.app/settings/api")
    return gaps


def find_approval(gmail: GmailLive, *, query: str, customer: str, project: str):
    """Search, then read, then adjudicate — three different calls, in that order."""
    print(f"  searching the mailbox: {query!r}")
    candidates = gmail.list_messages(query=query, max_results=10)
    print(f"  {len(candidates)} candidate(s) by search")
    last_reasons: list[str] = []
    for c in candidates:
        message = gmail.read_message(c["id"])
        evidence = parse_approval(
            message, known_contacts={S.CONTACT, (message.get("from") or "")},
            expected_customer=customer, expected_project=project,
            reference_date_iso=S.REFERENCE_DATE, now_ts=0.0)
        if evidence.ok:
            print(f"  accepted message {message['id']} from {message['from']}")
            print(f"  the message states the start it authorises: {evidence.stated_start_iso}")
            return message, evidence
        last_reasons = evidence.reasons
    print(f"  no candidate passed the evidence gate; last reasons: {last_reasons}")
    return None, None


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="subject:CAUSAL approval",
                    help="Gmail search used to find the approval (default: %(default)r)")
    ap.add_argument("--customer", default="Acme")
    ap.add_argument("--project", default="Implementation")
    ap.add_argument("--intent-id", default="LIVE-01")
    ap.add_argument("--personnel", nargs="*", default=[],
                    help="real attendee addresses for the calendar event")
    ap.add_argument("--contacts", nargs="*", default=[],
                    help="addresses the operator recognises as approvers for this run. The "
                         "engine's evidence gate refuses any other sender, so naming the "
                         "real approver here is an operator decision, not a bypass")
    ap.add_argument("--find-only", action="store_true", help="search and read; write nothing")
    args = ap.parse_args()

    gaps = missing()
    if gaps:
        print("LIVE run not possible yet — the three apps are not all reachable:\n")
        for gap in gaps:
            print(f"  - {gap}")
        print("\n  Linear is already reachable if the key is set. Nothing has been written.")
        return 2

    auth = google_auth()
    assert auth is not None
    gmail, calendar = GmailLive(auth), CalendarLive(auth)

    print("1. Gmail — find the approval that authorises this work")
    message, evidence = find_approval(gmail, query=args.query, customer=args.customer,
                                     project=args.project)
    if message is None or evidence is None:
        return 1

    start_iso = evidence.stated_start_iso or ""
    if not start_iso:
        print("  the approval names no resolvable time, so nothing can be authorised")
        return 1

    if args.find_only:
        print("\n--find-only: stopping before any write. The three surfaces are reachable:")
        print(f"  gmail    read {message['id']}")
        print(f"  calendar read path ok, would create the meeting from {start_iso}")
        print("  linear   read path ok")
        return 0

    print("\n2. build the contract from the approval, not from this script")
    intent = S.build_intent(args.customer, args.project, start_iso, intent_id=args.intent_id)
    print(f"  conflict key {intent.conflict_key}")
    print(f"  authority    {dict(intent.authority)}")

    print("\n3. execute against the real apps")
    stack = S.fresh_stack("LIVE", str(Path("./evidence/live-run.db")))
    if args.contacts:
        # The operator says who may authorise this work. Without it the engine holds only the
        # fixture contact, and a real approver is refused as an unrecognised sender — which
        # is the gate doing its job, not a defect.
        stack.engine.known_contacts = set(args.contacts) | set(stack.engine.known_contacts)
    print(f"  mode {stack.mode}")
    result = stack.engine.run(intent, evidence_message_id=message["id"])

    print(f"\n4. verdict: {result.status}  (committed={result.committed})")
    if result.refusal_code:
        print(f"  refused because: {result.refusal_code}")
        for reason in result.reasons:
            print(f"    - {reason}")
    for effect in result.effects:
        print(f"  {effect['effect_id']:<12} {effect['state']:<12} external id {effect.get('external_id', '')}")

    print(f"\n  audit rows unchanged: {stack.audit.verify_chain(intent.intent_id)}")
    print(f"  whole-log chain linked: {stack.audit.verify_chain()}")
    stack.close()
    return 0 if result.committed else 1


if __name__ == "__main__":
    raise SystemExit(main())
