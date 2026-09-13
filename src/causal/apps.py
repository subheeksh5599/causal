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
