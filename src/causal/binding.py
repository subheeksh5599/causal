"""Semantic effect identity: bind an intent to an effect the world already has.

Why this exists. Stripe takes an idempotency key. Gmail, Slack, Calendar and Linear
do not. If the response to a create is lost, the writer cannot tell whether its
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
