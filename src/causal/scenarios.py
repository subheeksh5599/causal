"""The sequences, as callable functions.

The CLI harness (`scripts/demo.py`) and the console API (`causal/api.py`) both
drive these, so what a judge clicks is the same code path the tests and the
harness exercise. One implementation, three ways to reach it.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from . import audit as A
from . import binding
from . import plain
from .apps import Apps
from .audit import Audit
from .engine import Causal
from .evidence_sink import OutcomeStore
from .intent import Intent, Scope, conflict_key_for, EffectSpec
from .ledger import EffectLedger
from .registry import Registry
def _fixture_clock() -> tuple[str, str, str, str]:
    """The demo world's dates, computed from a real date rather than frozen.

    The base is the Monday of the current week, so the week's Tuesday is unambiguous and
    the seeded approval and the intent it authorises always agree. None of these is a
    literal: a console opened next year shows next year's dates. The engine takes the
    clock as a parameter (`reference_date_iso`, `now_ts`) and never reaches for the wall
    clock inside a decision, so pinning a run for a reproducible quote is one env var:

        CAUSAL_REFERENCE_DATE=2026-09-14 uv run python scripts/demo.py

    Staleness is measured from each message's own timestamp, not from this date, so
    changing the base cannot make an approval look fresher than it is.
    """
    pinned = os.environ.get("CAUSAL_REFERENCE_DATE", "").strip()
    today = date.fromisoformat(pinned) if pinned else date.today()
    monday = today - timedelta(days=today.weekday())
    tuesday = monday + timedelta(days=1)
    wednesday = monday + timedelta(days=2)
    return (monday.isoformat(), f"{tuesday.isoformat()}T15:00",
            f"{tuesday.isoformat()}T16:00", f"{wednesday.isoformat()}T16:00")
