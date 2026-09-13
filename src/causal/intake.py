"""Natural-language intake: a request becomes a frozen intent.

Where the model lives, and where it does not. A proposer reads the sentence a human
wrote and offers a contract — fields, effects, a time. Everything after that is
deterministic: the schema, the operation whitelist, the authority mapping, the
conflict key, the freeze. A proposal is a suggestion; `validate_proposal` and this
module decide what survives.

What a proposal is not allowed to touch, and is rejected for trying:

    authority        the mapping from fact -> source of truth is the operator's, not
                     the model's. A model that can declare what counts as authority can
                     authorise itself.
    conflict_key     identity of the business outcome. A model that can choose this can
                     make two contending intents look like one, or one look like two.
    postconditions   the evidence an effect must satisfy. Declared by the system, per
                     app, before execution — never by the thing being checked.
    effects/apps     only from the operator's allowlist, and only known operations.

Proposers are pluggable:

    RuleProposer     deterministic, no network. Parses the canonical request form. Used
                     by the test suite and by any demo without a model key, and labelled
                     as such wherever the run is reported.
    HttpProposer     any OpenAI-shaped endpoint, when CAUSAL_MODEL_URL and
                     CAUSAL_MODEL_KEY are set. Thin client; the response goes through
                     exactly the same validation as the rule proposer.

The point of the split is that swapping a rule proposer for a model cannot widen what
the system will accept. That is testable, and `tests/test_h_intake.py` tests it.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .binding import canon
from .intent import (Intent, Scope, conflict_key_for, validate_proposal)

# The operator's authority map. Not the model's, and not negotiable at intake.
SYSTEM_AUTHORITY = {
    "approval": "gmail",
    "time": "gmail",
    "meeting": "calendar",
    "work": "linear",
}

DEFAULT_APPS = ("gmail", "calendar", "linear", "slack")

# Fields a proposal may never supply, with the reason each is refused.
RESERVED = {
    "authority": "the authority mapping is the operator's, so a proposal may not set it",
    "conflict_key": "business-outcome identity is derived, not proposed",
    "postconditions": "the evidence an effect must satisfy is declared by the system",
    "intent_hash": "identity is computed at freeze",
    "status": "state is the ledger's",
    "recipients": "who gets notified is an operator decision, never a proposer's",
}

_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})\b")
_SENTENCE = re.compile(r"^\s*(?P<customer>[A-Z][\w'&-]*(?:\s+[A-Z][\w'&-]*)*)\s+approved\s+the\s+"
                       r"(?P<project>[\w\s-]+?)\s*[.!]", re.IGNORECASE)


@dataclass
class Outcome:
    """What intake produced, and why it did or did not."""

    intent: Intent | None = None
    proposer: str = ""
    raw: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.intent is not None

    def as_dict(self) -> dict:
        return {"proposer": self.proposer, "accepted": self.accepted,
                "reasons": self.reasons,
                "intent_id": self.intent.intent_id if self.intent else "",
                "effects": [e.effect_id for e in (self.intent.effects if self.intent else ())],
                "proposal": self.raw}


# ------------------------------------------------------------------ proposers

class RuleProposer:
    """Deterministic parser for the canonical request form. No network, no model.

    Reads: "<Customer> approved the <project>. Kickoff <ISO time>." and offers the
    standard three-effect contract. Anything it cannot parse it declines to propose,
    which is the honest failure mode for a parser.
    """

    name = "rule-based"
    is_model = False

    def propose(self, text: str) -> dict:
        match = _SENTENCE.search(text or "")
        iso = _ISO.search(text or "")
        if not match or not iso:
            return {}
        project = match.group("project").strip()
        return {
            "customer": match.group("customer").strip(),
            "project": project.title(),
            "event": "Kickoff",
            "start_iso": iso.group(1),
            "resource": project.title(),
            "operation": "default",
            "effects": [
                {"effect_id": "CALENDAR-01", "app": "calendar", "operation": "CREATE_EVENT"},
                {"effect_id": "LINEAR-01", "app": "linear", "operation": "CREATE_ISSUE"},
                {"effect_id": "SLACK-01", "app": "slack", "operation": "POST_MESSAGE"},
            ],
        }


class HttpProposer:
    """Any OpenAI-shaped chat endpoint. A client, not a decision-maker."""

    name = "model"
    is_model = True

    def __init__(self, url: str | None = None, key: str | None = None,
                 model: str | None = None, timeout: float = 45.0) -> None:
        self.url = (url or os.environ.get("CAUSAL_MODEL_URL", "")).rstrip("/")
        self.key = key or os.environ.get("CAUSAL_MODEL_KEY", "")
        self.model = model or os.environ.get("CAUSAL_MODEL", "gpt-4o-mini")
        self.timeout = timeout
        if not self.url or not self.key:
            raise RuntimeError(
                "no model configured: set CAUSAL_MODEL_URL and CAUSAL_MODEL_KEY, or use "
                "the rule proposer (the run will say which one it used)")

    INSTRUCTIONS = (
        "Return JSON only. Fields: customer, project, event, start_iso (YYYY-MM-DDTHH:MM), "
        "resource, operation, effects (list of {effect_id, app, operation}). "
        "Allowed apps and operations: calendar/CREATE_EVENT, linear/CREATE_ISSUE, "
        "slack/POST_MESSAGE. Do not supply authority, conflict_key or postconditions."
    )

    def propose(self, text: str) -> dict:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": self.INSTRUCTIONS},
                         {"role": "user", "content": text}],
            "response_format": {"type": "json_object"},
        }).encode()
        request = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"model endpoint returned {exc.code}") from exc
        except Exception as exc:                                   # transport
            raise RuntimeError(f"model endpoint unreachable: {type(exc).__name__}") from exc
        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        try:
            return json.loads(content)
        except Exception as exc:
            raise RuntimeError(f"model did not return parseable JSON: {content[:120]!r}") from exc


class OverreachingProposer(RuleProposer):
    """A proposer that has decided it can widen its own authority.

    Shipped on purpose, because this is the shape of the interesting failure: a
    proposal that is otherwise well-formed and reaches for the fields that decide what
    it is allowed to do — rerouting which system adjudicates the work, and widening who
    gets contacted. The deterministic layer refuses it on the field, not on tone.
    """

    name = "rule+overreach"

    def propose(self, text: str) -> dict:
        raw = super().propose(text)
        raw["authority"] = {"approval": "gmail", "time": "gmail",
                            "meeting": "calendar", "work": "slack"}   # work: linear
        raw["recipients"] = ["everyone@example.com"]
        return raw


def default_proposer():
    """A model if one is configured, otherwise the deterministic parser."""
    try:
        return HttpProposer()
    except RuntimeError:
        return RuleProposer()


# --------------------------------------------------------------------- intake

def compile_intent(text: str, *, proposer=None, intent_id: str = "",
                   allowed_apps: tuple[str, ...] = DEFAULT_APPS,
                   recipients: tuple[str, ...] = ()) -> Outcome:
    """Text in, frozen intent out — or a list of reasons why not."""
    proposer = proposer or default_proposer()
    try:
        raw = proposer.propose(text)
    except RuntimeError as exc:
        return Outcome(proposer=getattr(proposer, "name", "?"), reasons=[str(exc)])

    reasons: list[str] = []

    if not isinstance(raw, dict) or not raw:
        return Outcome(proposer=getattr(proposer, "name", "?"), raw=raw or {},
                       reasons=["the proposer offered nothing parseable"])

    # 1. reserved fields, checked before anything else looks at the proposal
    for field_name, why in RESERVED.items():
        if field_name in raw:
            reasons.append(f"proposal sets {field_name!r}: {why}")
        for effect in raw.get("effects") or []:
            if isinstance(effect, dict) and field_name in effect:
                reasons.append(f"proposal sets {field_name!r} on an effect: {why}")

    # 2. the schema and the whitelists. The validator wants to see an authority map,
    #    and this is where the operator's is supplied — the proposal was already
    #    refused above if it tried to bring one of its own.
    to_validate = dict(raw)
    to_validate["authority"] = dict(SYSTEM_AUTHORITY)
    verdict = validate_proposal(to_validate, allowed_apps=set(allowed_apps))
    reasons.extend(verdict.reasons)

    if reasons or not verdict.ok or verdict.scope is None:
        return Outcome(proposer=getattr(proposer, "name", "?"), raw=raw, reasons=reasons)

    # 3. the contract is composed here, from the validated parts and the operator's
    #    policy — never from the proposal wholesale. Resource and operation are
    #    canonicalised because the exclusivity relation is matched on them.
    scope = Scope(
        customer=verdict.scope.customer, project=verdict.scope.project,
        event=verdict.scope.event, start_iso=verdict.scope.start_iso,
        recipients=tuple(recipients),
        allowed_apps=tuple(allowed_apps),
        resource=canon(str(raw.get("resource", "")))[:80],
        operation=canon(str(raw.get("operation", "")))[:40] or "default",
    )

    intent = Intent(
        intent_id=intent_id or f"NL-{abs(hash((scope.customer, scope.project, scope.event))) % 10**6:06d}",
        request=text,
        scope=scope,
        authority=dict(SYSTEM_AUTHORITY),
        effects=tuple(verdict.effects),
        conflict_key=conflict_key_for(scope),
    )
    intent.freeze()
    return Outcome(intent=intent, proposer=getattr(proposer, "name", "?"), raw=raw)
