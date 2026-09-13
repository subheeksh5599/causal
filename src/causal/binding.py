"""Semantic effect identity: bind an intent to an effect the world already has.

Why this exists. An API that accepts an idempotency key deduplicates for you.
These do not. If the response to a create is lost, the writer cannot tell whether its
effect exists, and the only safe question left is not "did my request succeed?" but
"does an effect matching this intent already exist out there?" That question has to
be answered against the external system's own state, by matching on the fields that
carry meaning, because there is no key the two sides agreed on.

Two binding paths, tried in this order:

  1. tagged binding   the effect carries the intent hash, so identity is exact
                      (this is what `reconcile.py` does)
  2. semantic binding no tag is present — the writer crashed before tagging, or a
                      foreign actor made an equivalent object — so identity is
                      established by comparing the intended effect against the
                      observable fields of candidates

Matchability is per surface, and it is the load-bearing policy in this module:

  COMPLETE     the read can enumerate the domain, so "no match" means "no effect"
  BOUNDED      the read enumerates only a retention window. Absence is provable
               inside that window, which is where reconciliation happens — a write
               we just attempted is always inside it — so this is still NOT_FOUND,
               recorded as bounded rather than exhaustive.
  BEST_EFFORT  the read is a search that is documented as not guaranteed complete.
               Absence is never provable here, at any window: NOT_PROVABLE.

On a surface that is not COMPLETE, "no match" is NOT_FOUND's dishonest cousin. It
is NOT_PROVABLE, and the difference matters more than any other distinction in the
system: NOT_FOUND is the branch that retries, and retrying is what creates the
duplicate this project exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

COMPLETE = "COMPLETE"
BOUNDED = "BOUNDED"
BEST_EFFORT = "BEST_EFFORT"

SURFACE_MATCHABILITY: dict[str, str] = {
    "calendar": COMPLETE,        # a window query enumerates the domain
    "linear": COMPLETE,          # a team-scoped filter enumerates the domain
    "slack": BOUNDED,            # history is paginated and retention-bounded
    "gmail": BEST_EFFORT,        # search is explicitly not guaranteed complete
}

# ---------------------------------------------------------------- match outcomes

NOT_FOUND = "NOT_FOUND"          # domain enumerated, nothing there: safe to retry
NOT_PROVABLE = "NOT_PROVABLE"    # absence cannot be established: never auto-retry
ONE_MATCH = "ONE_MATCH"          # exactly one effect matches: bind it
AMBIGUOUS = "AMBIGUOUS"          # more than one matches: refuse, a human decides
MISMATCH = "MISMATCH"            # one match, but it does not satisfy the contract
TAGGED = "TAGGED"                # exact binding by intent hash

# ------------------------------------------------------------------ canon rules
#
# Authored, deterministic, and deliberately dumb. The same intent must produce the
# same fingerprint in every worker, so nothing here may involve a model or a clock.
# Two workers disagreeing about identity would break the property the module exists
# to provide, which is why canonicalisation is a fixed rule set and not an inference.

_PUNCTUATION = ".,:;!?()[]{}\"'`*_-–—/\\|"


def canon(value: str | None) -> str:
    """Lowercase, drop punctuation, collapse whitespace. Nothing else."""
    if not value:
        return ""
    stripped = "".join(" " if ch in _PUNCTUATION else ch for ch in value)
    return " ".join(stripped.lower().split())


@dataclass(frozen=True)
class Fingerprint:
    """The meaning-carrying fields of an effect, canonicalised."""

    entity: str
    operation: str
    resource: str
    title: str
    start_iso: str = ""
    participants: tuple[str, ...] = ()

    @classmethod
    def of(cls, *, entity: str, operation: str, resource: str = "", title: str,
           start_iso: str | None = None, participants=()) -> "Fingerprint":
        return cls(
            entity=canon(entity),
            operation=canon(operation),
            resource=canon(resource),
            title=canon(title),
            start_iso=(start_iso or "")[:16],
            participants=tuple(sorted(canon(p) for p in participants if p)),
        )

    def as_dict(self) -> dict:
        return {"entity": self.entity, "operation": self.operation, "resource": self.resource,
                "title": self.title, "start_iso": self.start_iso,
                "participants": list(self.participants)}

    def digest(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def matches(self, observed: dict) -> bool:
        """Every field the intent cares about must be present and equal out there.

        Extra observed detail is ignored. That looseness is deliberate and it is the
        reason the false-binding rate is measured rather than assumed: a looser rule
        binds more effects and risks binding one that is not ours.
        """
        if self.title and canon(observed.get("title")) != self.title:
            return False
        if self.start_iso and (observed.get("start_iso") or "")[:16] != self.start_iso:
            return False
        if self.participants:
            seen = tuple(sorted(canon(p) for p in (observed.get("participants") or []) if p))
            if seen != self.participants:
                return False
        if self.resource:
            haystack = canon(" ".join(str(observed.get(k, "")) for k in
                                      ("title", "description", "text", "resource", "project")))
            if self.resource not in haystack:
                return False
        return True


@dataclass
class Verdict:
    outcome: str
    surface: str
    candidates: list[dict] = field(default_factory=list)
    matched: dict | None = None
    why: str = ""

    @property
    def bound(self) -> bool:
        return self.outcome in (ONE_MATCH, TAGGED)

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "surface": self.surface,
                "candidates": len(self.candidates),
                "matched_id": (self.matched or {}).get("id", ""), "why": self.why}


def classify(*, surface: str, intended: Fingerprint, candidates: list[dict],
             matchability: str | None = None) -> Verdict:
    """Decide what the external state says about this effect.

    `candidates` is whatever the surface returned for the neighbourhood of the
    intended effect. Nothing here trusts a write response.

    `matchability` overrides the module table, so an operator can mark a surface
    their own way rather than inheriting my judgement about it.
    """
    matched = [c for c in candidates if intended.matches(c)]

    if len(matched) == 1:
        return Verdict(ONE_MATCH, surface, candidates, matched[0],
                       "exactly one external object carries this intent's meaning")
    if len(matched) > 1:
        return Verdict(AMBIGUOUS, surface, candidates, None,
                       f"{len(matched)} external objects match this intent equally, "
                       "so ownership cannot be proved")

    how = (matchability or SURFACE_MATCHABILITY.get(surface, BEST_EFFORT)).upper()
    if how == BEST_EFFORT:
        return Verdict(NOT_PROVABLE, surface, candidates, None,
                       f"{surface} is BEST_EFFORT: its read is a search that cannot "
                       "prove the absence of a matching effect, so a retry would risk "
                       "creating a duplicate")
    if candidates:
        return Verdict(MISMATCH, surface, candidates, None,
                       "candidates exist in this domain but none matches the contract")
    why = ("the domain was enumerated and holds no matching effect" if how == COMPLETE
           else f"no match within {surface}'s retention window, which is where a write "
                "just attempted must appear")
    return Verdict(NOT_FOUND, surface, [], None, why)


# -------------------------------------------------------------- exclusivity policy
#
# Two operations on one resource that must never both be live. Authored by a human,
# versioned, and hashed, because a model deciding that "renew and cancel seem to
# conflict" is not a policy — it is a guess with a receipt.

EXCLUSIVITY_VERSION = "v1"

_EXCLUSIVE_PAIRS = (
    ("cancel_renew", "renew"),
    ("close_account", "create_account"),
    ("abort", "start"),
    ("revoke_offer", "send_offer"),
)


def _canonical_policy() -> list[list[str]]:
    return [sorted((canon(a), canon(b))) for a, b in
            sorted((canon(a), canon(b)) for a, b in _EXCLUSIVE_PAIRS)]


def exclusivity_digest() -> str:
    """Hash of the authored relation. Any edit changes it, so a frozen intent that
    recorded the old digest can be refused rather than silently re-policed."""
    blob = json.dumps({"version": EXCLUSIVITY_VERSION, "pairs": _canonical_policy()},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


EXCLUSIVITY_DIGEST = exclusivity_digest()


def exclusive(operation_a: str, operation_b: str) -> bool:
    pair = sorted((canon(operation_a), canon(operation_b)))
    return pair in _canonical_policy()


def exclusivity_conflict(a: str, b: str) -> bool:
    """Alias with a name that reads better at the call site."""
    return exclusive(a, b)
