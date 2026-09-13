"""Reconciliation: what to do when the external world's answer is unknown.

The rule that matters: UNKNOWN is never retried blindly. We read first.
Confidence is deterministic — four levels, no fuzzy matching, no model.

    EXACT      one object matches on every binding attribute      -> verify it
    LIKELY     one object matches on the strong attributes         -> extra checks
    AMBIGUOUS  more than one object plausibly matches             -> escalate to a human
    NOT_FOUND  nothing matches                                    -> retry is allowed
"""

from __future__ import annotations

from dataclasses import dataclass, field

EXACT = "EXACT"
LIKELY = "LIKELY"
AMBIGUOUS = "AMBIGUOUS"
NOT_FOUND = "NOT_FOUND"


@dataclass
class Match:
    confidence: str
    found: bool
    object_id: str = ""
    strategy: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"confidence": self.confidence, "found": self.found, "object_id": self.object_id,
                "strategy": self.strategy, "detail": self.detail}


def _strong(artifact: dict, expected: dict) -> int:
    """Count the strong attributes that agree.

    `operation` belongs here: two objects for the same customer and the same
    operation but a different intent hash are plausibly the same effect, which is
    what LIKELY means. Leaving it out made the LIKELY tier unreachable.
    """
    hits = 0
    for key in ("subject", "operation", "project", "intent_hash"):
        if key in expected and expected[key] and artifact.get(key) == expected[key]:
            hits += 1
    return hits


def reconcile(candidates: list[dict], expected: dict) -> Match:
    """Pure function. `candidates` is the independent read-back result."""
    if not candidates:
        return Match(NOT_FOUND, False, strategy="independent read returned no object")

    # 1. exact: intent hash + subject + operation all agree
    exact = [
        c for c in candidates
        if c.get("intent_hash") == expected.get("intent_hash")
        and c.get("subject", expected.get("subject")) == expected.get("subject")
        and c.get("operation", expected.get("operation")) == expected.get("operation")
    ]
    if len(exact) == 1:
        return Match(EXACT, True, exact[0].get("id", ""), "intent_hash+subject+operation",
                     {"matched": 1})
    if len(exact) > 1:
        return Match(AMBIGUOUS, True, strategy="multiple objects carry this intent hash",
                     detail={"matched": len(exact), "ids": [c.get("id") for c in exact]})

    # 2. likely: strong attributes agree on exactly one candidate
    strong = [c for c in candidates if _strong(c, expected) >= 2]
    if len(strong) == 1:
        return Match(LIKELY, True, strong[0].get("id", ""), "subject+project agreement",
                     {"matched": 1})
    if len(strong) > 1:
        return Match(AMBIGUOUS, True, strategy="several objects match on strong attributes",
                     detail={"matched": len(strong), "ids": [c.get("id") for c in strong]})

    return Match(NOT_FOUND, False, strategy="no object matched on strong attributes")


# policy applied to a reconciliation outcome
def action_for(confidence: str) -> str:
    return {
        EXACT: "VERIFY",
        LIKELY: "ADDITIONAL_CHECKS",
        AMBIGUOUS: "ESCALATE",
        NOT_FOUND: "RETRY_ALLOWED",
    }[confidence]
