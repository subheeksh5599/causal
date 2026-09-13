"""Plain English for the person who approves an outcome.

The engine's vocabulary is precise because precision is what makes it checkable:
EFFECTED, VERIFIED, AMBIGUOUS, NOT_PROVABLE, POST_COMMIT_DUPLICATE. None of it belongs
on a screen read by somebody who is not an engineer, and none of it should be softened
either — a duplicate is a duplicate.

So the translation keeps the distinction and drops the vocabulary. Nothing here
introduces a claim the system cannot support: every phrase maps to a state, and the
state is the proof.
"""
from __future__ import annotations
JOB = {
    "COMMITTED": "Done. Every system now agrees.",
    "REFUSED": "Stopped before anything changed.",
    "BLOCKED": "Stopped. Something has to be decided first.",
    "IDEMPOTENT": "Already done. This exact request had been made before.",
    "FROZEN": "Held. Changing an outcome that is already committed needs a person.",
}
EFFECT = {
    "PLANNED": "not started",
    "REQUESTED": "being written",
    "EFFECTED": "written, not yet confirmed",
    "VERIFYING": "being checked",
    "VERIFIED": "confirmed by reading it back from the system",
    "UNKNOWN": "written or not — nobody knows yet",
    "RECONCILING": "checking what actually happened",
    "NOT_FOUND": "not there",
    "NOT_PROVABLE": "could not be confirmed either way, so it was not repeated",
    "AMBIGUOUS": "two identical things exist and I cannot tell which one is ours",
    "AWAITING_APPROVAL": "waiting for a person to approve it",
    "POST_COMMIT_DUPLICATE": "a duplicate appeared afterwards",
    "VERIFICATION_FAILED": "there, but it does not match what was agreed",
    "REJECTED": "refused by the system",
    "ESCALATED": "handed to a person",
}
