"""The console API: one process, one command, no build step.

    uv run uvicorn causal.api:app --reload --port 8000

`GET /` serves the operator console. The sequences are buttons; every panel
is filled from a real run against the same engine the tests drive.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from . import audit as A
from . import intake
from . import plain
from . import scenarios
from .registry import CONFLICT, IDEMPOTENT, REGISTERED, RESUME, SUPERSEDE

CONSOLE = Path(__file__).with_name("console.html")
REVIEW = Path(__file__).with_name("review.html")

app = FastAPI(title="CAUSAL", description="Intent-bound cross-app execution")

_state: dict = {"stack": None}
#: which sequence produced each job, so approving can resume the run that made it
_origin: dict[str, str] = {}


def stack():
    if _state["stack"] is None:
        _state["stack"] = scenarios.fresh_stack(os.environ.get("CAUSAL_MODE", "LOCAL"),
                                                os.environ.get("CAUSAL_DB", "./evidence/console.db"))
    return _state["stack"]


@app.get("/", response_class=HTMLResponse)
def console() -> HTMLResponse:
    return HTMLResponse(CONSOLE.read_text())


@app.get("/review", response_class=HTMLResponse)
def review() -> HTMLResponse:
    """The same engine, for somebody who is not an engineer."""
    return HTMLResponse(REVIEW.read_text())


@app.get("/api/jobs")
def jobs() -> dict:
    """Every job, in words. Built from ledger state, never from a summary field."""
    s = stack()
    codes = {r["intent_id"]: r for r in s.outcomes.all()}
    out = []
    for row in s.registry.list_all():
        ih = row["intent_hash"]
        effects = s.ledger.all(ih) if ih else []
        approvals = s.ledger.approvals(ih) if ih else []
        view = plain.job_view(
            intent_id=row["intent_id"],
            status=row["status"],
            refusal_code=(codes.get(row["intent_id"]) or {}).get("refusal_code", ""),
            effects=effects, approvals=approvals,
            committed=row["status"] == "COMMITTED")
        view["app"] = coding_for(row["status"])
        view["sequence"] = _origin.get(row["intent_id"], "")
        out.append(view)
    return {"mode": s.mode, "jobs": out,
            "need_approval": [j for j in out if j["can_approve"]]}


@app.post("/api/approve")
def approve(body: dict) -> JSONResponse:
    """Record a named human's approval, then resume the run that was waiting.

    The approval is evidence about a person, so it is stored with who gave it. The
    engine will not write an outbound effect without one, and re-running the sequence
    is what turns the pending effect into a completed one.
    """
    s = stack()
    intent_id = str(body.get("intent_id", ""))
    who = str(body.get("approved_by", "")).strip()
    if not who:
        raise HTTPException(400, "an approval needs a named human")
    row = s.registry.get(intent_id)
    if row is None:
        raise HTTPException(404, f"unknown job {intent_id!r}")
    ih = row["intent_hash"]
    pending = [e for e in s.ledger.all(ih) if e["state"] == "AWAITING_APPROVAL"]
    if not pending:
        raise HTTPException(409, f"nothing awaits approval for {intent_id!r}")
    for e in pending:
        s.ledger.record_approval(intent_hash=ih, effect_id=e["effect_id"], approved_by=who,
                                 reason=str(body.get("reason", "")))
        s.audit.record(intent_id=intent_id, event_type=A.APPROVAL_GRANTED, intent_hash=ih,
                       new_state="REQUESTED", reason=f"approved by {who}")
    name = _origin.get(intent_id)
    if name is None:
        return JSONResponse({"intent_id": intent_id, "approved": [e["effect_id"] for e in pending],
                             "resumed": None})
    out = scenarios.run(name, s)
    out["metrics"] = _metrics(s)
    out["resumed"] = name
    return JSONResponse(out)


@app.post("/api/intake")
def intake_endpoint(body: dict) -> JSONResponse:
    """A request in words becomes a frozen contract — or a list of reasons why not.

    The proposer can be a model; it cannot widen what the system accepts. Ask with
    `adversarial: true` to see a proposal that reaches for its own authority and is
    refused on the field.
    """
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "a request is required")
    proposer = (intake.OverreachingProposer() if body.get("adversarial")
                else intake.default_proposer())
    outcome = intake.compile_intent(
        text, proposer=proposer,
        recipients=("dana.reyes@acme.example", "sam.okafor@acme.example"))
    payload: dict = {"proposer": outcome.proposer, "proposal": outcome.raw,
                     "accepted": outcome.intent is not None, "reasons": outcome.reasons,
                     "intent": None}   # always present, so a client can rely on the shape
    if outcome.intent is not None:
        it = outcome.intent
        payload["intent"] = {
            "intent_id": it.intent_id,
            "conflict_key": it.conflict_key,
            "authority": dict(it.authority),
            "effects": [e.effect_id for e in it.effects],
            "digest": (it.intent_hash or "")[:16],
        }
    return JSONResponse(payload)


@app.get("/api/health")
def health() -> dict:
    s = stack()
    return {"ok": True, "mode": s.mode, "apps": ["gmail", "calendar", "linear"]}


@app.get("/api/state")
def state() -> dict:
    s = stack()
    rows = _intents(s)
    return {
        "mode": s.mode,
        "sequences": [{"name": n, "title": t} for n, t in scenarios.SEQUENCES],
        "intents": rows,
        "metrics": _metrics(s),
    }


@app.post("/api/run/{name}")
def run_sequence(name: str) -> JSONResponse:
    if name == "all":
        return JSONResponse({"runs": [scenarios.run(n, stack()) for n, _ in scenarios.SEQUENCES],
                             "metrics": _metrics(stack())})
    if name not in {n for n, _ in scenarios.SEQUENCES}:
        raise HTTPException(404, f"unknown sequence {name!r}")
    out = scenarios.run(name, stack())
    _remember(name, out)
    out["metrics"] = _metrics(stack())
    return JSONResponse(out)


@app.post("/api/reset")
def reset() -> dict:
    old = _state.get("stack")
    if old is not None:
        old.close()
    db = Path(os.environ.get("CAUSAL_DB", "./evidence/console.db"))
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    _state["stack"] = None
    stack()
    return {"ok": True, "reset": True}


def _remember(name: str, out: dict) -> None:
    """Remember which sequence produced a job, so a later approval can resume it."""
    if out.get("intent_id"):
        _origin[out["intent_id"]] = name
    for run in out.get("runs") or []:
        _remember(name, run)
    for key in ("second", "third"):
        if isinstance(out.get(key), dict) and out[key].get("intent_id"):
            _origin[out[key]["intent_id"]] = name


def coding_for(status: str) -> str:
    """A colour, so a person can scan the list without reading every word."""
    if status == "COMMITTED":
        return "done"
    if status in ("REFUSED", "FROZEN"):
        return "stopped"
    return "waiting"


def _intents(s) -> list[dict]:
    """One row per intent the registry knows about, with its effect states."""
    rows = []
    for row in s.registry.list_all():
        effects = s.ledger.all(row["intent_hash"]) if row["intent_hash"] else []
        rows.append({
            "intent_id": row["intent_id"],
            "intent_hash": row["intent_hash"],
            "conflict_key": row["conflict_key"],
            "status": row["status"],
            "note": row["note"],
            "effects": [{"effect_id": e["effect_id"], "app": e["app"], "state": e["state"],
                         "external_id": e["external_id"], "attempts": e["attempts"]}
                        for e in effects],
            "committed": row["status"] == "COMMITTED",
        })
    return rows


def _metrics(s) -> dict:
    """Counters computed from the run, not asserted here.

    `false_commits` is measured: it walks every intent the registry calls
    COMMITTED and checks the ledger really holds verified effects for each
    required one. If a future change ever committed an unverified intent, this
    number would move, which is the point of computing it instead of writing 0.

    `commits_decided_by_a_model` is structurally zero: the engine, policy, ledger
    and registry have no model call site at all, which
    `tests/test_f_campaign.py::test_no_model_client_is_imported_in_the_decision_path`
    enforces by reading those modules' imports.
    """
    m = s.outcomes.metrics()
    false_commits = 0
    post_commit_duplicates = 0
    # A committed effect may legitimately end in POST_COMMIT_DUPLICATE: it was verified,
    # and something outside created an equivalent afterwards. That is a finding, not a
    # false commit, and conflating the two would make this counter accuse the system of
    # the thing it just caught.
    acceptable = {"VERIFIED", "POST_COMMIT_DUPLICATE"}
    for row in s.registry.list_all():
        if row["status"] != "COMMITTED":
            continue
        effects = s.ledger.all(row["intent_hash"]) if row["intent_hash"] else []
        if any(e["state"] == "POST_COMMIT_DUPLICATE" for e in effects):
            post_commit_duplicates += 1
        if not effects or any(e["state"] not in acceptable for e in effects):
            false_commits += 1
    m["false_commits"] = false_commits
    m["post_commit_duplicates"] = post_commit_duplicates
    # Read off the persisted per-run counters rather than typed in as zero: if any run
    # ever consulted a model hook, this number moves.
    m["commits_decided_by_a_model"] = m.get("model_calls", 0)
    return m


__all__ = ["app", "conflict_outcomes"]
conflict_outcomes = [REGISTERED, IDEMPOTENT, CONFLICT, SUPERSEDE, RESUME]
