#!/usr/bin/env python3
"""Run the flagship intent against real external services.

Three apps, one intent:

    Gmail twin      the approval that authorises the work   (read)
    Calendar twin   the kickoff event                       (write + independent read)
    Linear          a real issue in a real workspace        (write + independent read)

Gmail and Calendar come from stateful service twins: real services reached over HTTP,
with persistent state, unique ids, latency and failures, and no OAuth. Linear is
the production API.

The point of the exercise is that the protocol does not care which kind of service
is on the other end. Only base URLs change, and the run below proves the effects
landed by reading them back through a different call than the one that wrote them.

    uv run python scripts/twin_run.py

Twin sessions are short-lived on the free plan, so provisioning and the run happen
in one process. Connection details land in .twins/ (gitignored, chmod 600) because
they carry session credentials.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TWINS_DIR = ROOT / ".twins"
ENV = ROOT / ".env"
EVIDENCE = ROOT / "evidence"

CUSTOMER = "Acme"
PROJECT = "Implementation"
START_ISO = "2026-09-15T15:00"
INTENT_ID = "C-TWIN-1"
SEED_PROMPT = (
    "One unread email in the inbox. From buyer@acme.example to me. Subject "
    "'Approved'. Body exactly: 'Acme approved the implementation. Kickoff "
    "2026-09-15T15:00.' No other messages in the inbox."
)
TTL_MINUTES = 10


def load_env() -> None:
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def provision(name: str, scenario_prompt: str | None = None) -> dict:
    """Provision one twin, wait for ready, return its connection details."""
    base = (os.environ.get("TWIN_API_URL") or os.environ.get("TWIN_BASE_URL")
            or "").rstrip("/")
    key = os.environ.get("TWIN_API_KEY") or ""
    if not base or not key:
        sys.exit("TWIN_API_URL / TWIN_API_KEY missing (TWIN mode)")
    headers = {"Authorization": f"Bearer {key}"}
    body: dict = {"twins": [name], "ttl_minutes": TTL_MINUTES}
    if scenario_prompt:
        body["scenario_prompt"] = scenario_prompt
        body["scenario_generation_mode"] = "thorough"

    resp = httpx.post(f"{base}/validate/twins/provision", headers=headers, json=body, timeout=120)
    if resp.status_code >= 400:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:240]}"}
    run_id = resp.json()["run_id"]
    print(f"  {name}: provision run {run_id}")

    deadline = time.time() + 240
    while time.time() < deadline:
        status = httpx.get(f"{base}/validate/twins/provision/{run_id}/status",
                           headers=headers, timeout=60).json()
        state = status.get("status")
        if state in ("ready", "failed", "expired", "cancelled"):
            print(f"  {name}: {state}  (expires {status.get('expires_at')})")
            detail = dict((status.get("twins") or {}).get(name) or {})
            detail["_status"] = state
            detail["_expires_at"] = status.get("expires_at")
            detail["_proxy_token"] = status.get("proxy_token")
            return detail
        time.sleep(3)
    return {"error": "timed out waiting for ready"}


def token_from(detail: dict) -> str:
    """A twin hands back suggested env vars; use whichever carries the credential."""
    for key, value in (detail.get("env_vars") or {}).items():
        if isinstance(value, str) and value and any(t in key.upper() for t in ("TOKEN", "KEY", "SECRET")):
            return value
    return detail.get("_proxy_token") or "twin"


def main() -> int:
    load_env()
    TWINS_DIR.mkdir(exist_ok=True)
    os.chmod(TWINS_DIR, 0o700)

    print("provisioning twins")
    gmail = provision("gmail", SEED_PROMPT)
    calendar = provision("google_calendar")

    for name, detail in (("gmail", gmail), ("google_calendar", calendar)):
        if "error" in detail or detail.get("_status") != "ready":
            print(f"\n{name} is not usable: {detail.get('error') or detail.get('_status')}")
            (TWINS_DIR / "last_error.json").write_text(json.dumps(
                {"gmail": gmail, "google_calendar": calendar}, indent=2, default=str))
            return 1

    (TWINS_DIR / "env.json").write_text(json.dumps(
        {"gmail": gmail, "google_calendar": calendar}, indent=2, default=str))
    os.chmod(TWINS_DIR / "env.json", 0o600)

    os.environ["CAUSAL_MODE"] = "TWIN"
    os.environ["CAUSAL_TWIN_GMAIL_URL"] = gmail["base_url"]
    os.environ["CAUSAL_TWIN_GMAIL_TOKEN"] = token_from(gmail)
    os.environ["CAUSAL_TWIN_CALENDAR_URL"] = calendar["base_url"]
    os.environ["CAUSAL_TWIN_CALENDAR_TOKEN"] = token_from(calendar)

    # A fresh twin has no calendars, so `primary` does not resolve. Ask the twin for
    # one through its own API — the same step a real operator takes before scheduling.
    cal_base = calendar["base_url"].rstrip("/")
    cal_headers = {"Authorization": f"Bearer {token_from(calendar)}", "Content-Type": "application/json"}
    async_list = httpx.get(f"{cal_base}/calendar/v3/users/me/calendarList",
                           headers=cal_headers, timeout=30)
    items = async_list.json().get("items", []) if async_list.status_code < 300 else []
    if items:
        calendar_id = items[0]["id"]
        print(f"  calendar: using existing {calendar_id!r}")
    else:
        made = httpx.post(f"{cal_base}/calendar/v3/calendars", headers=cal_headers,
                          json={"summary": "CAUSAL"}, timeout=30)
        if made.status_code >= 300:
            print(f"  could not create a calendar on the twin: {made.status_code} {made.text[:160]}")
            return 1
        calendar_id = made.json()["id"]
        print(f"  calendar: created {calendar_id!r} (the twin ships with none)")
    os.environ["CAUSAL_TWIN_CALENDAR_ID"] = calendar_id

    from causal.scenarios import build_intent, fresh_stack

    EVIDENCE.mkdir(exist_ok=True)
    stack = fresh_stack("TWIN", db_path=str(EVIDENCE / "twin-run.db"))
    print(f"\n  gmail    {gmail['base_url']}")
    print(f"  calendar {calendar['base_url']}")
    print("  linear   https://api.linear.app/graphql  (production)")

    # --- the authority: read the approval out of the twin's inbox -------------
    print("\nreading the approval from the twin's inbox")
    found = stack.apps.mail.list_messages(query="Approved", max_results=10)
    if not found:
        found = stack.apps.mail.list_messages(max_results=10)
    print(f"  messages matched: {len(found)}")
    if not found:
        print("  the inbox is empty, so nothing authorises this work")
        return 1
    message = stack.apps.mail.read_message(found[0]["id"])
    print(f"  from:    {message['from']}")
    print(f"  subject: {message['subject']}")
    print(f"  body:    {message['body'][:140]}")

    # --- execute the intent --------------------------------------------------
    intent = build_intent(CUSTOMER, PROJECT, START_ISO, intent_id=INTENT_ID)
    print(f"\nexecuting intent {intent.intent_id} ({intent.intent_hash[:12]})")
    result = stack.engine.run(intent, evidence_message_id=message["id"])
    r = result.as_dict()

    print(f"\n  verdict:  {r['status']}   committed={r['committed']}")
    if r.get("refusal_code"):
        print(f"  refused:  {r['refusal_code']}")
    for reason in r.get("reasons") or []:
        print(f"    {reason}")
    for effect in r.get("effects") or []:
        print(f"  {effect.get('effect_id', '?'):<12} {effect.get('state', '?'):<20} "
              f"id={effect.get('external_id', '-')}  attempts={effect.get('attempts', 0)}")

    # --- verify by reading, through different calls than the writers ---------
    print("\nreading the effects back (different calls than the writers)")
    events = stack.apps.calendar.list_events(intent_hash=intent.intent_hash)
    tasks = stack.apps.linear.list_tasks(intent_hash=intent.intent_hash)
    print(f"  calendar events carrying this intent hash: {len(events)}")
    for ev in events:
        print(f"    {ev['id']}  {ev['title']!r}  {ev['start_iso']}")
    print(f"  linear issues carrying this intent hash:   {len(tasks)}")
    for task in tasks:
        print(f"    {task['id']}  {task['title']!r}")

    payload = {
        "mode": "TWIN + LIVE",
        "apps": {"authority": "gmail (stateful twin)", "scheduling": "google_calendar (stateful twin)",
                 "work": "linear (production API)"},
        "intent": {"id": intent.intent_id, "hash": intent.intent_hash,
                   "conflict_key": intent.conflict_key},
        "authority_message": {"from": message["from"], "subject": message["subject"]},
        "result": r,
        "read_back": {"calendar": events, "linear": tasks},
        "twins": {"gmail": {"base_url": gmail.get("base_url"), "expires_at": gmail.get("_expires_at")},
                  "calendar": {"base_url": calendar.get("base_url"),
                               "expires_at": calendar.get("_expires_at")}},
    }
    (EVIDENCE / "twin-run.json").write_text(json.dumps(payload, indent=2, default=str))
    stack.close()

    ok = r["committed"] and len(events) >= 1 and len(tasks) >= 1
    print("\nRESULT:", "committed, and both effects verified in the external systems" if ok
          else "not committed")
    print("artifact: evidence/twin-run.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
