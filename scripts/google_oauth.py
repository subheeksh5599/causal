#!/usr/bin/env python3
"""One-time Google OAuth: turn a consent click into a refresh token.

Why this exists: Gmail and Calendar hold private user data, so Google requires
OAuth. An API key cannot read either. This script runs a loopback consent flow,
exchanges the code for tokens, verifies BOTH APIs actually work, and appends the
refresh token to .env.

Two ways to run it.

Normal — it opens your browser and you click Allow:

    uv run python scripts/google_oauth.py

Split — for when the consent click happens somewhere else (another machine, or a
browser driven by something that is not this process). `--capture` prints the
URL, parks it in a file, and listens for the callback; you open the URL wherever
you like:

    uv run python scripts/google_oauth.py --capture
    # then, in any browser: open the URL it printed and click Allow

Either way the exchange, the verification of both APIs, and the .env write are
the same code path.

If either verification line fails, the fix is in the Google Cloud console, not
in the code — the message tells you which of the two common causes it is.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}/oauth2callback"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
URL_FILE = Path("/tmp/causal_oauth_url.txt")
CODE_FILE = Path("/tmp/causal_oauth_code.txt")


def load_env() -> None:
    """Read .env if the variables are not already exported."""
    if os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"):
        return
    if not ENV_PATH.exists():
        sys.exit(f"no {ENV_PATH} and no GOOGLE_CLIENT_ID in the environment")
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def append_env(pairs: dict[str, str]) -> None:
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    lines = [ln for ln in text.splitlines() if not any(ln.startswith(k + "=") for k in pairs)]
    lines += [f"{k}={v}" for k, v in pairs.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n")
    ENV_PATH.chmod(0o600)


def build_auth_url(client_id: str, state: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def _handler_for(state: str, captured: dict[str, str], on_code=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if params.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch, ignored")
                return
            captured["code"] = params.get("code", [""])[0]
            captured["error"] = params.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            body = ("<h2>Authorised. You can close this tab.</h2>"
                    if captured["code"] else f"<h2>Failed: {captured.get('error')}</h2>")
            self.wfile.write(body.encode())
            if on_code:
                threading.Thread(target=on_code, daemon=True).start()

        def log_message(self, *args) -> None:  # silence the access log
            return

    return Handler


def exchange_code(code: str) -> dict:
    """Trade the authorisation code for tokens, then verify both APIs."""
    import httpx

    client_id = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]

    token_resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=45,
    )
    payload = token_resp.json()
    if "refresh_token" not in payload:
        sys.exit(f"no refresh token in the response: {sorted(payload)} "
                 f"(revoke access at myaccount.google.com/permissions and re-run)")

    headers = {"Authorization": f"Bearer {payload.get('access_token', '')}"}
    problems: list[str] = []

    gmail = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                      headers=headers, timeout=30)
    if gmail.status_code == 200:
        who = gmail.json()
        print(f"  Gmail      OK  ({who.get('emailAddress')}, "
              f"{who.get('messagesTotal')} messages)")
    else:
        problems.append(f"Gmail returned {gmail.status_code}: {gmail.text[:200]}")

    cal = httpx.get("https://www.googleapis.com/calendar/v3/users/me/calendarList",
                    headers=headers, timeout=30)
    if cal.status_code == 200:
        print(f"  Calendar   OK  ({len(cal.json().get('items', []))} calendars visible)")
    else:
        problems.append(f"Calendar returned {cal.status_code}: {cal.text[:200]}")

    append_env({"GOOGLE_REFRESH_TOKEN": payload["refresh_token"]})
    print("\nrefresh token written to .env (chmod 600, gitignored)")

    if problems:
        print("\nthe consent worked, but:")
        for p in problems:
            print(f"  - {p}")
        print("\nmost likely: the Gmail API or Google Calendar API is not enabled for "
              "this project, or the scope was not granted. Enable both APIs and re-run.")
    else:
        print("\nboth APIs verified. Google is wired up.")
    return {"problems": problems, "refresh_token_present": True}


def main() -> int:
    load_env()
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing")

    capture = "--capture" in sys.argv
    state = secrets.token_urlsafe(24)
    captured: dict[str, str] = {}
    auth_url = build_auth_url(client_id, state)

    if capture:
        # Park the URL where another process can pick it up, then wait for the
        # callback. Nothing here opens a browser: whoever reads the file does.
        URL_FILE.write_text(auth_url)
        CODE_FILE.unlink(missing_ok=True)
        print(f"URL written to {URL_FILE}")
        print(auth_url)
        print(f"\n(listening on {REDIRECT_URI} for the callback)\n")

        def on_code() -> None:
            CODE_FILE.write_text(json.dumps(captured))

        server = HTTPServer(("127.0.0.1", PORT), _handler_for(state, captured, on_code))
        server.handle_request()
        if not captured.get("code"):
            CODE_FILE.unlink(missing_ok=True)
            sys.exit(f"no code returned: {captured.get('error') or 'unknown error'}")
        print("callback received")
        result = exchange_code(captured["code"])
        return 1 if result["problems"] else 0

    server = HTTPServer(("127.0.0.1", PORT), _handler_for(state, captured))
    print("\nSign in as the account that owns the Gmail inbox and the Calendar.\n")
    print(auth_url)
    print(f"\n(listening on {REDIRECT_URI} for the callback)\n")
    threading.Thread(target=webbrowser.open, args=(auth_url,), daemon=True).start()
    server.handle_request()

    if not captured.get("code"):
        sys.exit(f"no code returned: {captured.get('error') or 'unknown error'}")
    result = exchange_code(captured["code"])
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
