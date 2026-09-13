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
