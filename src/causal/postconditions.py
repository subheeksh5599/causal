"""Deterministic postconditions. These are pre-registered on the intent, and
they are plain comparisons. A model may propose which claims to doubt; it never
decides whether a claim holds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DAY = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_TIME = r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{2}:\d{2})"
_CLAIM = re.compile(rf"{_DAY}\s*(?:at)?\s*{_TIME}", re.I)
_ISOISH = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?")


@dataclass
class Verdict:
    ok: bool
    violations: list[str]

    def as_dict(self) -> dict:
        return {"ok": self.ok, "violations": list(self.violations)}


def authorised_phrasings(start_iso: str) -> list[str]:
    """Every acceptable rendering of the one authorised time."""
    from datetime import datetime

    dt = datetime.fromisoformat(start_iso)
    day = dt.strftime("%A").lower()
    hhmm = dt.strftime("%H:%M")
    hour12 = dt.strftime("%I").lstrip("0") or "12"
    meridiem = dt.strftime("%p").lower()
    return [
        f"{day} at {hhmm}",
        f"{day} {hhmm}",
        f"{day} at {hour12}{meridiem}",
        f"{day} at {hour12} {meridiem}",
        start_iso,
    ]


def check_temporal_claims(text: str, start_iso: str, *, require_present: bool = False) -> list[str]:
    """An artifact may not make a temporal claim that disagrees with the scope.

    The rule is "must not disagree", not "must be present". A bare task title
    that names no time is not a violation — only claiming a *different* time is.
    `require_present` exists for artifacts where a time is mandatory by nature.
    """
    allowed = authorised_phrasings(start_iso)
    violations: list[str] = []
    found_any = False
    for m in _CLAIM.finditer(text):
        found_any = True
        day = m.group(1).lower()
        if not any(day in a and m.group(2).replace(" ", "") in a.replace(" ", "") for a in allowed):
            violations.append(f"artifact claims {m.group(0)!r}, not authorised by {start_iso}")
    for m in _ISOISH.finditer(text):
        found_any = True
        if m.group(0) != start_iso:
            violations.append(f"artifact states date {m.group(0)!r}, not authorised by {start_iso}")
    if require_present and not found_any:
        violations.append("artifact makes no temporal claim at all")
    return violations


def check_calendar(artifact: dict, *, intent, effect) -> Verdict:
    v: list[str] = []
    expected_title = f"{intent.scope.customer} {intent.scope.project} {intent.scope.event}"
    if artifact.get("title", "").lower() != expected_title.lower():
        v.append(f"event title {artifact.get('title')!r} != authorised {expected_title!r}")
    if artifact.get("start_iso") != intent.scope.start_iso:
        v.append(f"event start {artifact.get('start_iso')!r} != authorised {intent.scope.start_iso!r}")
    # Compare the ADDRESS-shaped recipients only, on both sides. A contract may name a Slack
    # channel ("#engineering") among its recipients, and a channel can never be a calendar
    # invitee: the live adapter filters those out, so comparing against the raw recipient set
    # failed every real event on a check the artifact cannot possibly satisfy. Every address
    # the contract authorises must still be on the invite, and no other address may be.
    invited = {r.lower() for r in intent.scope.recipients if "@" in r}
    present = {a.lower() for a in artifact.get("attendees", []) if "@" in a}
    if present != invited:
        v.append("event attendees do not match the authorised recipients")
    if artifact.get("intent_hash") != intent.intent_hash:
        v.append("event does not carry this intent's hash")
    return Verdict(not v, v)


def check_linear(artifact: dict, *, intent, effect) -> Verdict:
    v: list[str] = []
    if artifact.get("project", "").lower() != intent.scope.project.lower():
        v.append(f"task project {artifact.get('project')!r} != authorised {intent.scope.project!r}")
    if intent.scope.event.lower() not in artifact.get("title", "").lower():
        v.append("task title does not reference the authorised event")
    if artifact.get("intent_hash") != intent.intent_hash:
        v.append("task does not carry this intent's hash")
    # the same rule as every other artifact: no temporal claim may disagree
    v.extend(check_temporal_claims(f"{artifact.get('title', '')} {artifact.get('description', '')}",
                                   intent.scope.start_iso))
    return Verdict(not v, v)


def check_slack(artifact: dict, *, intent, effect) -> Verdict:
    v: list[str] = []
    text = artifact.get("text", "")
    low = text.lower()
    if intent.scope.customer.lower() not in low:
        v.append("message does not name the authorised customer")
    if intent.scope.project.lower() not in low:
        v.append("message does not name the authorised project")
    if artifact.get("intent_hash") != intent.intent_hash:
        v.append("message does not carry this intent's hash")
    v.extend(check_temporal_claims(text, intent.scope.start_iso))
    return Verdict(not v, v)


CHECKERS = {"calendar": check_calendar, "linear": check_linear, "slack": check_slack}


def check(app: str, artifact: dict, *, intent, effect) -> Verdict:
    fn = CHECKERS.get(app)
    if fn is None:
        return Verdict(False, [f"no postcondition checker registered for app {app!r}"])
    return fn(artifact, intent=intent, effect=effect)
