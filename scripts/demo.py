"""CAUSAL - the six sequences, run end to end.

    uv run python scripts/demo.py                     # LOCAL: in-process state
    CAUSAL_MODE=LIVE uv run python scripts/demo.py    # real Gmail / Calendar / Linear

This is the verification harness, not the show. It prints the numbers the
submission quotes and writes one artifact per sequence to evidence/.

Two notes on how it is built:

  * Each sequence uses a DIFFERENT business outcome (customer/project/event).
    They have to: the conflict registry is doing its job, so two sequences
    sharing an outcome would have the second one refused before it runs.
  * Every number printed is read back from the apps. False commits, duplicate
    writes and independent read-back are COMPUTED here from the runs above, not
    written down as constants.
  * This harness prints the six original sequences with bespoke narration. The
    binding sequences (takeover, duplicate intent, ambiguous effect, post-commit
    duplicate, not-provable absence, counter-intent) live in causal.scenarios and
    are driven by the console, the API and the test suite.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from causal import Causal, EffectSpec                      # noqa: E402
from causal.adapters import build_apps                     # noqa: E402
from causal.apps import Apps                               # noqa: E402
from causal.audit import Audit                             # noqa: E402
from causal.evidence_sink import OutcomeStore              # noqa: E402
from causal.intent import Intent, Scope, conflict_key_for  # noqa: E402
from causal.ledger import EffectLedger                     # noqa: E402
from causal.registry import Registry                       # noqa: E402
