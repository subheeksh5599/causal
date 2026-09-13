"""The adversarial campaign: many randomised runs, invariants asserted on every one.

The deterministic suite proves specific things in specific orders. This proves the
properties hold when the ORDER and the FAULTS are not chosen by the author.

Each iteration picks a fault profile per effect at random, runs the real engine, and
then asserts the invariants below against the resulting state, the external world,
and the audit chain. A seed is printed for every failure, so any counterexample is
reproducible with --seed.

    uv run python scripts/campaign.py --runs 100

Invariants checked after every run:

  I1  COMMITTED implies every required effect is VERIFIED
  I2  at most one external artifact exists per (intent hash, effect)
  I3  the engine consulted a model zero times
  I4  the audit chain verifies, and its links are intact
  I5  no effect was retried from a state that forbids retrying
  I6  reconciliation never created a second artifact
  I7  every refusal names a code
  I8  a committed run's artifacts all carry the intent hash
  I9  nothing commits when the authority evidence is absent
"""
from __future__ import annotations
import argparse
import json
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from causal import ledger as L                                    # noqa: E402
from causal.apps import PermanentError, TransientError            # noqa: E402
from causal.scenarios import CONTACT, REFERENCE_DATE, build_intent, fresh_stack, truth  # noqa: E402
EVIDENCE = ROOT / "evidence"
