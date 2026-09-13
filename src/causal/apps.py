"""Four apps as in-process services. Write path and read path are deliberately different methods.

The verifier is never handed the write response. It can only call the read path,
which is a different endpoint than the one that wrote. That is the structural
reason an HTTP 200 cannot become a success claim.

Fault injection has two flavours, and the difference is the whole point:

  fail_before(n)     the effect never happened      -> clean retry is correct
  fail_after(n)      the effect HAPPENED, we never
                     saw confirmation               -> UNKNOWN; must reconcile by
                                                       reading before any retry
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class TransientError(Exception):
    """Transport-level failure: 429 / 5xx / timeout / no response."""


class PermanentError(Exception):
    """Refused by the app: do not retry."""


# ---------------------------------------------------------------------------
# The interfaces the engine programs against. The in-process services below and the live
# adapters in adapters.py both satisfy these, which is why swapping LOCAL for
# LIVE or TWIN changes one environment variable and no engine code.
# ---------------------------------------------------------------------------

@runtime_checkable
class MailClient(Protocol):
    name: str
    def read_message(self, message_id: str) -> dict: ...
    def list_messages(self, *, query: str = "", max_results: int = 10) -> list[dict]: ...


@runtime_checkable
class CalendarClient(Protocol):
    name: str
    def insert_event(self, *, title: str, start_iso: str, attendees: tuple[str, ...],
                     intent_hash: str) -> dict: ...
    def list_events(self, *, intent_hash: str) -> list[dict]: ...


@runtime_checkable
class LinearClient(Protocol):
    name: str
    def create_task(self, *, project: str, title: str, intent_hash: str) -> dict: ...
    def list_tasks(self, *, intent_hash: str) -> list[dict]: ...


@runtime_checkable
class SlackClient(Protocol):
    name: str
    def post_message(self, *, channel: str, text: str, intent_hash: str) -> dict: ...
    def list_messages(self, *, channel: str, intent_hash: str) -> list[dict]: ...


@dataclass
class World:
    """Deterministic state for all four apps. One object, four services."""

    mail: dict[str, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    _ids: dict = field(default_factory=dict)

    def next_id(self, prefix: str) -> str:
        n = self._ids.get(prefix, 0) + 1
        self._ids[prefix] = n
        return f"{prefix}-{n:04d}"


class _Base:
    name: str

    def __init__(self, app: str, world: World) -> None:
        self.app = app
        self.world = world
        self._fail_before = 0
        self._fail_after = 0

    def fail_before(self, n: int = 1) -> None:
        self._fail_before = n

    def fail_after(self, n: int = 1) -> None:
        self._fail_after = n

    def _gate(self, *, after: bool) -> None:
        if self._fail_before > 0:
            self._fail_before -= 1
            raise TransientError(f"{self.app}: transport failure before the effect landed")
        if after and self._fail_after > 0:
            self._fail_after -= 1
            raise TransientError(
                f"{self.app}: no response received — the request may or may not have landed"
            )


# ---------------------------------------------------------------------------
# Gmail — the authority for approval and for the authorised time
# ---------------------------------------------------------------------------

class MailApp(_Base):
    def __init__(self, world: World) -> None:
        super().__init__("gmail", world)

    def seed_message(self, message_id: str, *, sender: str, body: str,
                     timestamp: float | None = None) -> None:
        self.world.mail[message_id] = {"id": message_id, "from": sender, "body": body,
                                       "timestamp": timestamp}

    # read path
    def read_message(self, message_id: str) -> dict:
        try:
            return dict(self.world.mail[message_id])
        except KeyError as exc:
            raise PermanentError(f"gmail: no such message {message_id}") from exc

    def list_messages(self, *, query: str = "", max_results: int = 10) -> list[dict]:
        """Same contract as the live client: which messages match, before reading any.

        The local services are not searched by query (there is no index to search),
        so an ignored query is reported rather than silently returning everything.
        """
        needle = query.lower()
        out = []
        for message_id, payload in self.world.mail.items():
            haystack = f"{payload.get('from', '')} {payload.get('body', '')}".lower()
            if needle and needle not in haystack:
                continue
            out.append({"id": message_id, "thread_id": message_id})
        return out[:max_results]


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

class CalendarApp(_Base):
    def __init__(self, world: World) -> None:
        super().__init__("calendar", world)

    # write path
    def insert_event(self, *, title: str, start_iso: str, attendees: tuple[str, ...], intent_hash: str) -> dict:
        self._gate(after=False)
        ev = {
            "id": self.world.next_id("evt"),
            "title": title,
            "start_iso": start_iso,
            "attendees": list(attendees),
            "intent_hash": intent_hash,
        }
        self.world.events.append(ev)
        # the effect HAS landed; the response is what we lose
        self._gate(after=True)
        return ev

    # read path — a different endpoint than insert
    def list_events(self, *, intent_hash: str) -> list[dict]:
        return [dict(e) for e in self.world.events if e.get("intent_hash") == intent_hash]

    # semantic read path — the neighbourhood of an intended effect, tagged or not.
    # The untagged ones are the point: an outside actor creates them.
    def search_candidates(self, *, start_iso: str = "", title: str = "") -> list[dict]:
        day = (start_iso or "")[:10]
        return [
            {"id": e["id"], "title": e.get("title", ""),
             "start_iso": (e.get("start_iso") or "")[:16],
             "participants": list(e.get("attendees") or []),
             "intent_hash": e.get("intent_hash", "")}
            for e in self.world.events
            if not day or (e.get("start_iso") or "")[:10] == day
        ]

    def foreign_event(self, *, title: str, start_iso: str, attendees=()) -> dict:
        """An actor outside this system writes an equivalent object with no tag."""
        ev = {"id": self.world.next_id("ext"), "title": title, "start_iso": start_iso,
              "attendees": list(attendees), "intent_hash": "", "foreign": True}
        self.world.events.append(ev)
        return dict(ev)


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------

class LinearApp(_Base):
    def __init__(self, world: World) -> None:
        super().__init__("linear", world)

    # write path
    def create_task(self, *, project: str, title: str, intent_hash: str) -> dict:
        self._gate(after=False)
        t = {
            "id": self.world.next_id("lin"),
            "project": project,
            "title": title,
            "intent_hash": intent_hash,
        }
        self.world.tasks.append(t)
        self._gate(after=True)          # landed, response lost
        return t

    # read path
    def list_tasks(self, *, intent_hash: str) -> list[dict]:
        return [dict(t) for t in self.world.tasks if t.get("intent_hash") == intent_hash]

    def search_candidates(self, *, resource: str = "", title: str = "") -> list[dict]:
        return [
            {"id": t["id"], "title": t.get("title", ""), "project": t.get("project", ""),
             "resource": t.get("resource", ""), "intent_hash": t.get("intent_hash", "")}
            for t in self.world.tasks
        ]

    def foreign_task(self, *, title: str, project: str = "", resource: str = "") -> dict:
        t = {"id": self.world.next_id("ext"), "project": project, "title": title,
             "resource": resource, "intent_hash": "", "foreign": True}
        self.world.tasks.append(t)
        return dict(t)


# ---------------------------------------------------------------------------
# Slack — notification is evidence of awareness, never authority
# ---------------------------------------------------------------------------

class SlackApp(_Base):
    def __init__(self, world: World) -> None:
        super().__init__("slack", world)

    def seed_message(self, *, channel: str, text: str, intent_hash: str) -> dict:
        """Pre-existing state in the app, as if something else wrote it."""
        msg = {
            "id": self.world.next_id("msg"),
            "channel": channel,
            "text": text,
            "intent_hash": intent_hash,
            "seeded": True,
        }
        self.world.messages.append(msg)
        return msg

    # write path
    def post_message(self, *, channel: str, text: str, intent_hash: str) -> dict:
        self._gate(after=False)
        msg = {
            "id": self.world.next_id("msg"),
            "channel": channel,
            "text": text,
            "intent_hash": intent_hash,
        }
        self.world.messages.append(msg)
        self._gate(after=True)          # landed, response lost
        return msg

    # read path
    def list_messages(self, *, channel: str, intent_hash: str) -> list[dict]:
        return [
            dict(m)
            for m in self.world.messages
            if m.get("channel") == channel and m.get("intent_hash") == intent_hash
        ]

    def search_candidates(self, *, channel: str = "", title: str = "") -> list[dict]:
        return [
            {"id": m["id"], "text": m.get("text", ""), "channel": m.get("channel", ""),
             "intent_hash": m.get("intent_hash", "")}
            for m in self.world.messages
            if not channel or m.get("channel") == channel
        ]

    def foreign_message(self, *, channel: str, text: str) -> dict:
        m = {"id": self.world.next_id("ext"), "channel": channel, "text": text,
             "intent_hash": "", "foreign": True}
        self.world.messages.append(m)
        return dict(m)


@dataclass
class Apps:
    mail: Any
    calendar: Any
    linear: Any
    slack: Any
    """Each field is the client for that app: a Protocol implementation (the
    in-process services), a live adapter, or `Unavailable` when credentials for
    that app are absent. Typed as Any because the refusing client satisfies the
    protocols dynamically — that refusal is the behaviour, not a type hole."""

    @staticmethod
    def local() -> "Apps":
        w = World()
        return Apps(MailApp(w), CalendarApp(w), LinearApp(w), SlackApp(w))

    def by_name(self, name: str):
        return {"gmail": self.mail, "calendar": self.calendar, "linear": self.linear,
                "slack": self.slack}[name]
