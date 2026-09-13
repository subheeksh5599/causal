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
