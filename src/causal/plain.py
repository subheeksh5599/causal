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

REFUSAL = {
    "CONFLICT": "another job is already responsible for this outcome",
    "EVIDENCE_MISSING": "the approval this depends on could not be found",
    "EVIDENCE_STALE": "the approval is too old to rely on",
    "AUTHORITY_MISMATCH": "the approval came from a system with no authority over this",
    "OUT_OF_SCOPE": "it reached outside what was approved",
    "AMBIGUOUS_EXTERNAL_STATE": "two identical things exist, so ownership cannot be proven",
    "NOT_PROVABLE": "it could not be confirmed, so nothing was repeated",
    "AWAITING_APPROVAL": "a person has to approve this first",
    "EXCLUSIVITY_CONFLICT": "it contradicts another job on the same thing",
    "POST_COMMIT_DUPLICATE": "a duplicate appeared after the job was finished",
    "IDEMPOTENT": "this exact request had already been made",
    "SUPERSEDED": "it changed an outcome that was already committed",
    "NOT_COMMITTED": "not everything could be confirmed",
    "STALE_AUTHORIZATION": "the authorisation has expired",
    "POSTCONDITION_FAILED": "what was written does not match what was agreed",
    "UNSUPPORTED_OPERATION": "that action is not in the allowed set",
    "ILLEGAL_TRANSITION": "the system refused an inconsistent step",
    "VERIFICATION_FAILED": "a system disagreed about what it holds",
}


def phrase_effect(state: str) -> str:
    return EFFECT.get(state, state.lower().replace("_", " "))


def phrase_refusal(code: str) -> str:
    return REFUSAL.get(code, (code or "").lower().replace("_", " "))


def phrase_status(status: str) -> str:
    return JOB.get(status, status.lower().replace("_", " "))


def job_view(*, intent_id: str, status: str, refusal_code: str, effects: list[dict],
             approvals: list[dict], committed: bool) -> dict:
    """One job, as a person would describe it."""
    lines = [{"app": e["app"], "effect": e["effect_id"], "state": e["state"],
              "plain": phrase_effect(e["state"])} for e in effects]
    confirmed = sum(1 for e in effects if e["state"] == "VERIFIED")
    waiting = [e["effect_id"] for e in effects if e["state"] == "AWAITING_APPROVAL"]
    contested = [e["effect_id"] for e in effects if e["state"] == "AMBIGUOUS"]
    unproven = [e["effect_id"] for e in effects if e["state"] == "NOT_PROVABLE"]

    if committed and not waiting and not contested:
        headline = f"Done. {confirmed} of {len(effects)} systems confirmed."
    elif waiting:
        headline = f"Waiting for your approval to send {', '.join(waiting)}."
    elif contested:
        headline = "Two identical things exist. I stopped rather than guess."
    elif unproven:
        headline = "I could not confirm one of these, so I did not repeat it."
    else:
        headline = phrase_status(status)

    return {
        "intent_id": intent_id,
        "headline": headline,
        "detail": phrase_refusal(refusal_code) if refusal_code else "",
        "status": status,
        "confirmed": confirmed,
        "of": len(effects),
        "effects": lines,
        "needs_approval": waiting,
        "contested": contested,
        "unproven": unproven,
        "approved_by": [a["approved_by"] for a in approvals],
        "can_approve": bool(waiting),
    }
