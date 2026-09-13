"""Live adapters for the four apps, with the read path deliberately separate
from the write path in every one of them.

Design rules enforced here:

  * The verifier never receives a write response. `list_*` are different calls
    against different endpoints than `insert_*` / `create_*` / `post_*`.
  * Every effect carries the intent hash into the external object, so the
    read-back can prove the object belongs to this intent:
        Calendar -> extendedProperties.private.intent_hash
        Linear   -> "[CAUSAL:<hash>]" in the description
        Slack    -> "[CAUSAL:<hash>]" in the message text
  * HTTP status maps to TransientError (429/5xx/transport) or PermanentError
    (other 4xx). Only transient failures are retryable, and only after
    reconciliation.
  * Tokens are read from the environment and never logged, echoed or embedded.

Every client takes an optional `base_url`. Pointing that at an Arga twin gives
you the identical code path against a stateful service replica instead of the
production API — that is the LIVE / TWIN switch, and it is one environment
variable, not a code change.
"""

from __future__ import annotations

import base64
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

from .apps import PermanentError, TransientError

INTENT_TAG = "CAUSAL"


class TokenSource(Protocol):
    """Anything that can produce a bearer token: OAuth refresh, or a static
    twin token."""

    def token(self) -> str: ...


_ANGLE = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")
_LOOSE = re.compile(r"[^\s<>,;\"]+@[^\s<>,;\"]+")


def address_of(value: str) -> str:
    """The address a message actually came from.

    A real Gmail `From` header is a display name plus an address —
    `"Reyes, Dana" <dana@acme.com>` — while the evidence gate compares against bare
    addresses. Identity is the address, so the adapter normalises it here rather than
    making the gate learn one provider's header syntax.

    The angle-bracket form is taken first and unconditionally: a display name that
    impersonates a trusted address (`"buyer@acme.example" <attacker@evil.test>`) must
    resolve to the attacker, never to the name it is wearing.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    found = _ANGLE.search(raw)
    if found:
        return found.group(1).strip().lower()
    loose = _LOOSE.search(raw)
    return (loose.group(0) if loose else raw).strip().lower()


def _hash_tag(intent_hash: str) -> str:
    return f"[{INTENT_TAG}:{intent_hash[:8]}]"


def _request(method: str, url: str, *, headers: dict, json_body=None, data=None, timeout: float = 30.0):
    import httpx

    try:
        resp = httpx.request(method, url, headers=headers, json=json_body, data=data, timeout=timeout)
    except Exception as exc:                                   # transport layer
        raise TransientError(f"transport failure: {type(exc).__name__}") from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientError(f"HTTP {resp.status_code} {resp.text[:160]}")
    if resp.status_code >= 400:
        raise PermanentError(f"HTTP {resp.status_code} {resp.text[:200]}")
    try:
        return resp.json()
    except Exception as exc:
        raise PermanentError(f"non-JSON response: {resp.text[:120]}") from exc


# ---------------------------------------------------------------------------
# Google: one OAuth client covers Gmail and Calendar
# ---------------------------------------------------------------------------

class GoogleAuth:
    """Refresh-token → access-token, cached in memory for its lifetime."""

    def __init__(self, *, client_id: str, client_secret: str, refresh_token: str) -> None:
        self._id, self._secret, self._refresh = client_id, client_secret, refresh_token
        self._token = ""
        self._expires_at = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        body = _request(
            "POST", "https://oauth2.googleapis.com/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"client_id": self._id, "client_secret": self._secret,
                  "refresh_token": self._refresh, "grant_type": "refresh_token"},
        )
        self._token = body["access_token"]
        self._expires_at = time.time() + float(body.get("expires_in", 3600))
        return self._token


class GmailLive:
    """Read-only. In this design Gmail is the authority for approval, never a target."""

    name = "gmail"
    base_url_default = "https://gmail.googleapis.com"

    def __init__(self, auth: TokenSource, *, base_url: str | None = None) -> None:
        self.auth = auth
        self.base_url = (base_url or self.base_url_default).rstrip("/")
        self.name = "gmail"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.auth.token()}"}

    def read_message(self, message_id: str) -> dict:
        payload = _request("GET", f"{self.base_url}/gmail/v1/users/me/messages/{message_id}",
                           headers=self._headers()) or {}
        headers = {h.get("name", "").lower(): h.get("value", "")
                   for h in (payload.get("payload", {}).get("headers") or [])}
        body = self._body(payload.get("payload", {}))
        internal = payload.get("internalDate")
        return {
            "id": payload.get("id", message_id),
            "from": address_of(headers.get("from", "")),
            "subject": headers.get("subject", ""),
            "body": body,
            "timestamp": (float(internal) / 1000.0) if internal else None,
        }

    def list_messages(self, *, query: str = "", max_results: int = 10) -> list[dict]:
        """Read path: which messages match, before reading any of them.

        A different endpoint and a different call than read_message, so finding
        the approval and verifying the approval are never the same request.
        """
        from urllib.parse import urlencode

        qs = urlencode({"maxResults": max_results, **({"q": query} if query else {})})
        data = _request("GET", f"{self.base_url}/gmail/v1/users/me/messages?{qs}",
                        headers=self._headers()) or {}
        return [{"id": m.get("id", ""), "thread_id": m.get("threadId", "")}
                for m in (data.get("messages") or [])]

    @staticmethod
    def _body(part: dict) -> str:
        if part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"] + "===").decode(errors="ignore")
        for child in part.get("parts") or []:
            if child.get("mimeType") == "text/plain":
                return GmailLive._body(child)
        for child in part.get("parts") or []:
            text = GmailLive._body(child)
            if text:
                return text
        return ""


class CalendarLive:
    name = "calendar"
    base_url_default = "https://www.googleapis.com"

    def __init__(self, auth: TokenSource, *, calendar_id: str = "primary",
                 timezone: str = "Europe/London", base_url: str | None = None) -> None:
        self.auth = auth
        self.calendar_id = calendar_id
        self.timezone = timezone
        self.base_url = (base_url or self.base_url_default).rstrip("/")
        self.name = "calendar"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.auth.token()}", "Content-Type": "application/json"}

    # write path
    def insert_event(self, *, title: str, start_iso: str, attendees: tuple[str, ...],
                     intent_hash: str) -> dict:
        from datetime import datetime, timedelta

        start = datetime.fromisoformat(start_iso)
        end = start + timedelta(hours=1)
        body = {
            "summary": title,
            "description": f"{_hash_tag(intent_hash)} created by CAUSAL",
            "start": {"dateTime": start.isoformat(), "timeZone": self.timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": self.timezone},
            "attendees": [{"email": a} for a in attendees if "@" in a],
            "extendedProperties": {"private": {"intent_hash": intent_hash}},
        }
        return _request("POST", f"{self.base_url}/calendar/v3/calendars/{self.calendar_id}/events",
                        headers=self._headers(), json_body=body)

    # read path — a different endpoint, filtered by the intent hash
    def list_events(self, *, intent_hash: str) -> list[dict]:
        params = f"privateExtendedProperty=intent_hash%3D{intent_hash}&maxResults=50&singleEvents=true"
        data = _request(
            "GET",
            f"{self.base_url}/calendar/v3/calendars/{self.calendar_id}/events?{params}",
            headers=self._headers(),
        ) or {}
        out = []
        for ev in data.get("items", []):
            private = ((ev.get("extendedProperties") or {}).get("private") or {})
            out.append({
                "id": ev.get("id", ""),
                "title": ev.get("summary", ""),
                "start_iso": ((ev.get("start") or {}).get("dateTime") or "")[:16],
                "attendees": [a.get("email", "") for a in (ev.get("attendees") or [])],
                "intent_hash": private.get("intent_hash", ""),
            })
        return out


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------

class LinearLive:
    name = "linear"
    base_url_default = "https://api.linear.app/graphql"

    def __init__(self, api_key: str, *, team_id: str, base_url: str | None = None) -> None:
        self._key, self.team_id = api_key, team_id
        self.base_url = base_url or self.base_url_default

    def _headers(self) -> dict:
        return {"Authorization": self._key, "Content-Type": "application/json"}

    def _gql(self, query: str, variables: dict) -> dict:
        body = _request("POST", self.base_url, headers=self._headers(),
                        json_body={"query": query, "variables": variables})
        if body.get("errors"):
            raise PermanentError(f"linear: {body['errors'][0].get('message', 'error')[:160]}")
        return body.get("data") or {}

    # write path
    def create_task(self, *, project: str, title: str, intent_hash: str) -> dict:
        data = self._gql(
            "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
            "{ success issue { id identifier title description } } }",
            {"input": {"teamId": self.team_id, "title": title,
                       "description": f"{_hash_tag(intent_hash)} project={project}"}},
        )
        issue = ((data.get("issueCreate") or {}).get("issue")) or {}
        return {"id": issue.get("identifier") or issue.get("id", ""),
                "title": issue.get("title", ""),
                "project": project,
                "intent_hash": intent_hash}

    # read path
    def list_tasks(self, *, intent_hash: str) -> list[dict]:
        data = self._gql(
            "query($tag: String!) { issues(filter: { description: { contains: $tag } }, first: 50) "
            "{ nodes { id identifier title description } } }",
            {"tag": _hash_tag(intent_hash)},
        )
        out = []
        for node in ((data.get("issues") or {}).get("nodes") or []):
            desc = node.get("description") or ""
            project = ""
            for token in desc.split():
                if token.startswith("project="):
                    project = token.split("=", 1)[1]
            out.append({"id": node.get("identifier") or node.get("id", ""),
                        "title": node.get("title", ""),
                        "project": project,
                        "intent_hash": intent_hash})
        return out


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

class SlackLive:
    name = "slack"
    base_url_default = "https://slack.com/api"

    def __init__(self, bot_token: str, *, channel_id: str = "", base_url: str | None = None) -> None:
        self._token, self.channel_id = bot_token, channel_id
        self.base_url = (base_url or self.base_url_default).rstrip("/")
        self._channel_cache: dict[str, str] = {}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json; charset=utf-8"}

    def resolve_channel(self, name: str) -> str:
        """'#engineering' -> C0123... , because the API wants an id."""
        if self.channel_id:
            return self.channel_id
        if name in self._channel_cache:
            return self._channel_cache[name]
        want = name.lstrip("#")
        data = _request("POST", f"{self.base_url}/conversations.list",
                        headers=self._headers(), json_body={"limit": 200}) or {}
        for ch in data.get("channels", []):
            if ch.get("name") == want:
                self._channel_cache[name] = ch["id"]
                return ch["id"]
        raise PermanentError(f"slack: no channel named {want!r} is visible to this bot")

    # write path
    def post_message(self, *, channel: str, text: str, intent_hash: str) -> dict:
        data = _request("POST", f"{self.base_url}/chat.postMessage", headers=self._headers(),
                        json_body={"channel": self.resolve_channel(channel),
                                   "text": f"{text} {_hash_tag(intent_hash)}"})
        if not data.get("ok"):
            raise PermanentError(f"slack: {data.get('error', 'unknown error')}")
        return {"id": data.get("ts", ""), "channel": data.get("channel", ""),
                "text": text, "intent_hash": intent_hash}

    # read path
    def list_messages(self, *, channel: str, intent_hash: str) -> list[dict]:
        data = _request("POST", f"{self.base_url}/conversations.history", headers=self._headers(),
                        json_body={"channel": self.resolve_channel(channel), "limit": 100}) or {}
        if not data.get("ok"):
            raise PermanentError(f"slack: {data.get('error', 'unknown error')}")
        tag = _hash_tag(intent_hash)
        return [{"id": m.get("ts", ""), "channel": channel, "text": m.get("text", ""),
                 "intent_hash": intent_hash}
                for m in data.get("messages", []) if tag in (m.get("text") or "")]


# ---------------------------------------------------------------------------
# factory: LIVE, TWIN, or LOCAL
# ---------------------------------------------------------------------------

@dataclass
class Mode:
    """LOCAL uses in-process state. LIVE uses production APIs. TWIN swaps the
    base URL for a stateful service replica and needs no OAuth at all."""

    kind: str = "LOCAL"


class Unavailable:
    """A service with no credentials configured.

    Every call refuses with the reason, so a missing integration is a loud,
    specific failure rather than an empty result that looks like success.
    """

    def __init__(self, name: str, reason: str) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "reason", reason)

    def __getattr__(self, item: str):
        if item.startswith("_"):
            raise AttributeError(item)

        def _refuse(*_a, **_k):
            raise PermanentError(f"{self.name}: unavailable — {self.reason}")

        return _refuse


class StaticAuth:
    """Bearer token for twin-hosted services, where there is no OAuth dance."""

    def __init__(self, token: str = "twin") -> None:
        self._token = token

    def token(self) -> str:
        return self._token


def _slack_client(*, twin: bool = False):
    """Slack is optional in this build. A Slack twin needs no credentials at all,
    and without either, the client refuses loudly instead of reporting silence."""
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    if twin and os.environ.get("CAUSAL_TWIN_SLACK_URL"):
        return SlackLive(os.environ.get("CAUSAL_TWIN_SLACK_TOKEN", "twin"), channel_id=channel,
                         base_url=os.environ["CAUSAL_TWIN_SLACK_URL"])
    token = os.environ.get("SLACK_BOT_TOKEN")
    if token:
        return SlackLive(token, channel_id=channel)
    return Unavailable("slack", "no SLACK_BOT_TOKEN and no Slack twin configured")


def build_apps(mode: str | None = None):
    """Return the Apps bundle for the requested mode: LOCAL, LIVE or TWIN."""
    from .apps import Apps

    mode = (mode or os.environ.get("CAUSAL_MODE", "LOCAL")).upper()

    if mode == "LOCAL":
        return Apps.local()

    timezone = os.environ.get("CAUSAL_TIMEZONE", "Europe/London")

    if mode == "LIVE":
        auth = GoogleAuth(
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        )
        return Apps(
            mail=GmailLive(auth),
            calendar=CalendarLive(auth, calendar_id=os.environ.get("GOOGLE_CALENDAR_ID", "primary"),
                                  timezone=timezone),
            linear=LinearLive(os.environ["LINEAR_API_KEY"], team_id=os.environ["LINEAR_TEAM_ID"]),
            slack=_slack_client(),
        )

    if mode == "TWIN":
        # Gmail and Calendar as stateful replicas: no Google credentials at all.
        # Same client classes, same methods; only the host changes.
        mail_auth = StaticAuth(os.environ.get("CAUSAL_TWIN_GMAIL_TOKEN", "twin"))
        cal_auth = StaticAuth(os.environ.get("CAUSAL_TWIN_CALENDAR_TOKEN", "twin"))
        return Apps(
            mail=GmailLive(mail_auth, base_url=os.environ["CAUSAL_TWIN_GMAIL_URL"]),
            calendar=CalendarLive(cal_auth, timezone=timezone,
                                  calendar_id=os.environ.get("CAUSAL_TWIN_CALENDAR_ID", "primary"),
                                  base_url=os.environ["CAUSAL_TWIN_CALENDAR_URL"]),
            linear=LinearLive(os.environ["LINEAR_API_KEY"], team_id=os.environ["LINEAR_TEAM_ID"]),
            slack=_slack_client(twin=True),
        )

    raise ValueError(f"unknown CAUSAL_MODE {mode!r} (expected LOCAL, LIVE or TWIN)")
