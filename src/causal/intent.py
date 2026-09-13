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
_CLOCK = re.compile(
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.I,
)
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class Evidence:
    ok: bool
    source_app: str
    message_id: str
    approved: bool
    stated_start_iso: str | None
    content_hash: str
    observed_at: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "source_app": self.source_app, "message_id": self.message_id,
                "approved": self.approved, "stated_start_iso": self.stated_start_iso,
                "content_hash": self.content_hash, "observed_at": self.observed_at,
                "reasons": self.reasons}


def parse_approval(message: dict, *, known_contacts: set[str], expected_customer: str,
                   expected_project: str, reference_date_iso: str | None = None,
                   now_ts: float = 0.0) -> Evidence:
    """Plain string/regex checks. No model is consulted for a fact."""
    reasons: list[str] = []
    body = message.get("body") or ""
    low = body.lower()
    sender = (message.get("from") or "").strip().lower()

    if sender not in {c.lower() for c in known_contacts}:
        reasons.append(f"sender {sender!r} is not a recognised customer contact")
    approved = any(p in low for p in APPROVAL_PHRASES)
    if not approved:
        reasons.append("no approval phrase present")
    if expected_customer.lower() not in low:
        reasons.append(f"customer {expected_customer!r} not named in the message")
    if expected_project.lower() not in low:
        reasons.append(f"project {expected_project!r} not named in the message")

    stated = None
    m = _ISO.search(body)
    if m:
        stated = m.group(1)
    else:
        c = _CLOCK.search(body)
        if c:
            if not reference_date_iso:
                reasons.append("a weekday-only time cannot be resolved without a reference date")
            else:
                from datetime import date, timedelta

                day, hour, minute, merid = c.group(1).lower(), int(c.group(2)), int(c.group(3) or 0), c.group(4).lower()
                ref = date.fromisoformat(reference_date_iso[:10])
                delta = (_DAYS.index(day) - ref.weekday()) % 7 or 7
                target = ref + timedelta(days=delta)
                if merid == "pm" and hour != 12:
                    hour += 12
                if merid == "am" and hour == 12:
                    hour = 0
                stated = f"{target.isoformat()}T{hour:02d}:{minute:02d}"
        if stated is None and not any(r.startswith("no resolvable") for r in reasons):
            reasons.append("no resolvable date/time in the approval message")

    content_hash = hashlib.sha256(
        json.dumps({"id": message.get("id"), "from": sender, "body": body}, sort_keys=True).encode()
    ).hexdigest()[:32]

    # staleness is measured from the message's own timestamp, never from the
    # moment we happened to read it
    observed = message.get("timestamp")
    observed_at = float(observed) if observed else now_ts

    return Evidence(ok=not reasons, source_app="gmail", message_id=message.get("id", ""),
                    approved=approved, stated_start_iso=stated, content_hash=content_hash,
                    observed_at=observed_at, reasons=reasons)


# ---------------------------------------------------------------------------
# model output validation: the model proposes, this file disposes
# ---------------------------------------------------------------------------

REQUIRED_PROPOSAL_FIELDS = ("customer", "project", "event", "start_iso", "effects")


@dataclass
class Proposal:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    scope: Scope | None = None
    effects: tuple[EffectSpec, ...] = ()
    authority: dict[str, str] = field(default_factory=dict)


def validate_proposal(raw: dict, *, allowed_apps: set[str]) -> Proposal:
    """Schema + semantic validation of whatever the model produced."""
    reasons: list[str] = []
    if not isinstance(raw, dict):
        return Proposal(False, ["proposal is not an object"])

    for f in REQUIRED_PROPOSAL_FIELDS:
        if not raw.get(f):
            reasons.append(f"missing required field {f!r}")

    effects_in = raw.get("effects") or []
    if not isinstance(effects_in, list) or not effects_in:
        reasons.append("effects must be a non-empty list")
        effects_in = []

    seen: set[str] = set()
    specs: list[EffectSpec] = []
    for e in effects_in:
        if not isinstance(e, dict):
            reasons.append("effect entry is not an object")
            continue
        eid, app = e.get("effect_id", ""), e.get("app", "")
        if not eid or not app:
            reasons.append("effect requires effect_id and app")
            continue
        if eid in seen:
            reasons.append(f"duplicate effect_id {eid!r}")
        seen.add(eid)
        if app not in allowed_apps:
            reasons.append(f"unsupported app {app!r}")
        op = e.get("operation", "CREATE")
        if op not in ALLOWED_OPERATIONS.get(app, set()):
            reasons.append(f"unsupported operation {op!r} for app {app!r}")
        specs.append(EffectSpec(effect_id=eid, app=app, operation=op,
                                target=e.get("target", ""),
                                postconditions=tuple(e.get("postconditions") or ())))

    start = raw.get("start_iso") or ""
    if start and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", start):
        reasons.append(f"malformed start_iso {start!r}")

    tz = raw.get("timezone")
    if tz is not None and not re.fullmatch(r"[A-Za-z]+/[A-Za-z_]+", str(tz)):
        reasons.append(f"malformed timezone {tz!r}")

    authority = raw.get("authority") or {}
    if not authority:
        reasons.append("authority map is empty")

    if reasons:
        return Proposal(False, reasons)

    scope = Scope(
        customer=raw["customer"], project=raw["project"], event=raw["event"],
        start_iso=start,
        recipients=tuple(raw.get("recipients") or ()),
        allowed_apps=tuple(sorted(allowed_apps)),
    )
    return Proposal(True, [], scope, tuple(specs), dict(authority))
