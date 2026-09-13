#!/usr/bin/env python
"""Prove, per surface, whether CAUSAL can actually talk to it — and say what is missing.

    uv run python scripts/verify_live.py            # reads only; safe, no side effects
    uv run python scripts/verify_live.py --write    # also creates, then reads back

This exists because "wired, not yet exercised" is a claim that should be checkable.
Each surface is reported as LIVE (a real round trip succeeded), UNCONFIGURED (the
credential is absent, with the one command that fixes it), or FAILED (it is configured
and did not work — the only outcome that is a defect).

Read-only by default. Nothing here prints a credential, and a token never reaches
stdout: the checks report ids and counts, not requests.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from causal.adapters import CalendarLive, GmailLive, GoogleAuth, LinearLive  # noqa: E402

LIVE, UNCONFIGURED, FAILED = "LIVE", "UNCONFIGURED", "FAILED"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def google_auth() -> GoogleAuth | None:
    cid, secret, refresh = (_env("GOOGLE_CLIENT_ID"), _env("GOOGLE_CLIENT_SECRET"),
                            _env("GOOGLE_REFRESH_TOKEN"))
    if not (cid and secret and refresh):
        return None
    return GoogleAuth(client_id=cid, client_secret=secret, refresh_token=refresh)


def check_linear(write: bool) -> tuple[str, str]:
    key, team = _env("LINEAR_API_KEY"), _env("LINEAR_TEAM_ID")
    if not (key and team):
        return UNCONFIGURED, "LINEAR_API_KEY and LINEAR_TEAM_ID unset"
    try:
        client = LinearLive(key, team_id=team)
        probe = "verify-live"          # a hash no intent uses, so the read proves auth
        found = client.list_tasks(intent_hash=probe)
        if not write:
            return LIVE, f"read path ok (team {team[:8]}…, {len(found)} tagged task(s))"
        title = f"CAUSAL live verification {int(time.time())}"
        created = client.create_task(project="CAUSAL verification", title=title,
                                     intent_hash=probe)
        seen = client.list_tasks(intent_hash=probe)
        if not any(t.get("id") == created.get("id") for t in seen):
            return FAILED, "a created issue could not be read back"
        return LIVE, f"wrote {created.get('identifier')} and read it back independently"
    except Exception as exc:                                   # noqa: BLE001 - reported
        return FAILED, f"{type(exc).__name__}: {exc}"


def check_gmail(write: bool) -> tuple[str, str]:
    auth = google_auth()
    if auth is None:
        return UNCONFIGURED, ("GOOGLE_REFRESH_TOKEN unset — one consent click: "
                              "uv run python scripts/google_oauth.py --capture")
    try:
        client = GmailLive(auth)
        found = client.list_messages(query="newer_than:30d", max_results=5)
        if not write:
            return LIVE, f"read path ok ({len(found)} recent message(s))"
        return LIVE, (f"read path ok ({len(found)} recent message(s)); send path is "
                      "exercised by the demo, not here")
    except Exception as exc:                                   # noqa: BLE001
        return FAILED, f"{type(exc).__name__}: {exc}"


def check_calendar(write: bool) -> tuple[str, str]:
    auth = google_auth()
    if auth is None:
        return UNCONFIGURED, ("GOOGLE_REFRESH_TOKEN unset — one consent click: "
                              "uv run python scripts/google_oauth.py --capture")
    try:
        client = CalendarLive(auth)
        found = client.list_events(intent_hash="verify-live")
        if not write:
            return LIVE, f"read path ok ({len(found)} event(s) carrying a CAUSAL tag)"
        stamp = time.strftime("%Y-%m-%dT%H:%M")
        created = client.insert_event(title="CAUSAL live verification", start_iso=stamp,
                                      attendees=(),  intent_hash="verify-live")
        seen = client.list_events(intent_hash="verify-live")
        if not any(e.get("external_id") == created.get("external_id") for e in seen):
            return FAILED, "an inserted event could not be read back"
        return LIVE, f"wrote {created.get('external_id')} and read it back independently"
    except Exception as exc:                                   # noqa: BLE001
        return FAILED, f"{type(exc).__name__}: {exc}"


def check_google_client() -> tuple[str, str]:
    """Are the OAuth client credentials themselves valid?

    A deliberately invalid code proves it: Google answers `invalid_grant` when the
    client is fine and the code is not, and `invalid_client` when the credentials are
    wrong. So this separates "misconfigured" from "nobody has consented yet" — which is
    the whole question for the Google surfaces.
    """
    cid, secret = _env("GOOGLE_CLIENT_ID"), _env("GOOGLE_CLIENT_SECRET")
    if not (cid and secret):
        return UNCONFIGURED, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET unset"
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret, "code": "deliberately-invalid",
        "grant_type": "authorization_code",
        "redirect_uri": "http://127.0.0.1:8765/",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    try:
        urllib.request.urlopen(req, timeout=20)                # pragma: no cover
        return FAILED, "an invalid code was accepted, which should be impossible"
    except urllib.error.HTTPError as exc:
        payload = _json.loads(exc.read().decode() or "{}")
        err = payload.get("error", "")
        if err == "invalid_grant":
            return LIVE, "credentials accepted by Google; only the consent click is missing"
        return FAILED, f"Google rejected the client itself: {err or exc.code}"
    except Exception as exc:                                   # noqa: BLE001
        return FAILED, f"{type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="also create and read back")
    args = ap.parse_args()

    checks = (("linear", check_linear), ("gmail", check_gmail), ("calendar", check_calendar),
              ("google-oauth", lambda _w: check_google_client()))
    results = {}
    print("CAUSAL live surface verification")
    print(f"  mode: {'write + read' if args.write else 'read only'}\n")
    for name, fn in checks:
        status, detail = fn(args.write)
        results[name] = status
        print(f"  {name:<9} {status:<13} {detail}")

    live = [n for n, s in results.items() if s == LIVE]
    unconfigured = [n for n, s in results.items() if s == UNCONFIGURED]
    failed = [n for n, s in results.items() if s == FAILED]
    print(f"\n  {len(live)} live, {len(unconfigured)} unconfigured, {len(failed)} failed")
    if failed:
        print(f"\n  DEFECT: {', '.join(failed)} is configured and did not work.")
        return 1
    if unconfigured:
        print(f"  not a defect: {', '.join(unconfigured)} has no credential in this "
              "environment, so no claim is made about it either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
