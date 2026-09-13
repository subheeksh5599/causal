"""Intent contracts: the thing that is authorized, frozen, and later proven.

Two design decisions worth stating, because both are load-bearing:

* The conflict key is the BUSINESS OUTCOME IDENTITY ONLY (customer/project/
  event). It deliberately excludes the disputed fact — the time. If the
  disputed value were inside the key, two intents that disagree about it would
  never collide and the conflict detector would be dead code that always
  reports CLEAR.

* The intent hash is computed once, over the canonical form. Everything
  external later has to prove it belongs to that hash.
"""
from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
class FrozenIntentError(RuntimeError):
    """Raised when anything tries to mutate an intent after it is frozen."""


# The complete set of operations this system is permitted to perform. An
# unknown operation is refused before any adapter is consulted.
ALLOWED_OPERATIONS: dict[str, set[str]] = {
    "calendar": {"CREATE_EVENT"},
    "linear": {"CREATE_ISSUE"},
    "slack": {"POST_MESSAGE"},
    "gmail": set(),
}


@dataclass(frozen=True)
class Scope:
    """The narrow authority of one intent. Outside this, nothing is allowed."""

    customer: str
    project: str
    event: str
    start_iso: str                                # the disputed fact; adjudicated by authority
    recipients: tuple[str, ...] = ()
    allowed_apps: tuple[str, ...] = ("gmail", "calendar", "linear", "slack")
    # What real-world thing this intent acts on, and how. Deliberately NOT part of
    # conflict_key: two intents can disagree about the date and still contend for the
    # same resource, which is what the exclusivity relation checks.
    resource: str = ""
    operation: str = ""

    def allows(self, *, customer: str, project: str, event: str) -> bool:
        norm = lambda s: s.strip().lower()  # noqa: E731
        return (norm(customer) == norm(self.customer) and norm(project) == norm(self.project)
                and norm(event) == norm(self.event))


@dataclass(frozen=True)
class EffectSpec:
    """One external consequence. `postconditions` are declared BEFORE execution.

    `payload` is what the MODEL proposed for this effect's content. It is never
    authoritative: the scope is. A payload that contradicts the frozen scope is
    exactly how a write can succeed while the action fails.
    """

    effect_id: str
    app: str
    operation: str
    target: str = ""                              # channel / team / attendee, if any
    postconditions: tuple[str, ...] = ()
    payload: tuple[tuple[str, str], ...] = ()     # frozen key/value pairs (hashable)

    def payload_dict(self) -> dict:
        return {k: v for k, v in self.payload}


@dataclass
class Intent:
    intent_id: str
    request: str
    scope: Scope
    authority: Mapping[str, str]                  # fact -> authoritative app
    effects: tuple[EffectSpec, ...]
    conflict_key: str
    expires_at_ts: float | None = None
    approval_max_age_s: float | None = None
    intent_hash: str = ""
    authority_hash: str = ""
    status: str = "DRAFT"
    _frozen: bool = field(default=False, repr=False)

    # -- identity ------------------------------------------------------
    def canonical(self) -> dict:
        return {
            "scope": {
                "customer": self.scope.customer,
                "project": self.scope.project,
                "event": self.scope.event,
                "start_iso": self.scope.start_iso,
                "recipients": sorted(self.scope.recipients),
                "allowed_apps": sorted(self.scope.allowed_apps),
            },
            "authority": dict(sorted(self.authority.items())),
            "effects": sorted(
                (
                    {"effect_id": e.effect_id, "app": e.app, "operation": e.operation,
                     "target": e.target, "postconditions": sorted(e.postconditions)}
                    for e in self.effects
                ),
                key=lambda d: d["effect_id"],
            ),
        }

    @staticmethod
    def _canonical_json(payload: dict) -> str:
        """Normalise before hashing: sorted keys, collapsed whitespace, tight separators."""
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return re.sub(r"\s+", "", blob)

    def freeze(self) -> str:
        self.intent_hash = hashlib.sha256(
            self._canonical_json(self.canonical()).encode()
        ).hexdigest()[:32]
        self.authority_hash = hashlib.sha256(
            self._canonical_json(dict(sorted(self.authority.items()))).encode()
        ).hexdigest()[:32]
        # freeze the authority map itself: a plain dict could still be mutated
        # in place by anything holding a reference, which would let authority
        # change without the hash changing.
        from types import MappingProxyType

        self.authority = MappingProxyType(dict(self.authority))
        self._frozen = True
        self.status = "AUTHORIZED"
        return self.intent_hash

    def assert_frozen(self) -> None:
        if not self._frozen:
            raise FrozenIntentError(f"intent {self.intent_id} is not frozen")

    def guard_mutation(self, field_name: str) -> None:
        if self._frozen:
            raise FrozenIntentError(
                f"intent {self.intent_id} is frozen; refusing to change {field_name}")

    def set_authority(self, authority: dict[str, str]) -> None:
        self.guard_mutation("authority")
        self.authority = dict(authority)

    @property
    def required_effects(self) -> tuple[EffectSpec, ...]:
        return self.effects

    def short_hash(self) -> str:
        return (self.intent_hash or "")[:4].upper()
def conflict_key_for(scope: Scope) -> str:
    """Business-outcome identity. The disputed fact is NOT part of this."""
    return "::".join(
        part.strip().upper().replace(" ", "_") for part in (scope.customer, scope.project, scope.event)
    )


# ---------------------------------------------------------------------------
# deterministic evidence gate
# ---------------------------------------------------------------------------

APPROVAL_PHRASES = ("approved", "approves", "accepts", "accepted", "signed off", "go ahead")
_ISO = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})")
