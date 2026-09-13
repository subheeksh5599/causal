"""Prove, per surface, whether CAUSAL can actually talk to it — and say what is missing.

    uv run python scripts/verify_live.py            # reads only; safe, no side effects
    uv run python scripts/verify_live.py --write    # also creates, then reads back

This exists because "wired, not yet exercised" is a claim that should be checkable.
Each surface is reported as LIVE (a real round trip succeeded), UNCONFIGURED (the
credential is absent, with the one command that fixes it), or FAILED (it is configured
and did not work — the only outcome that is a defect).

Read-only by default. Nothing here prints a credential, and a token never reaches
stdout: the checks report ids and counts, not requests.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
