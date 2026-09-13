"""The one command a judge runs to check every claim in this repository.

    uv run python scripts/verify_all.py            # everything
    uv run python scripts/verify_all.py --quick    # skip the 100-run campaign

A README asserts things. This turns the assertions into a gate: it runs the suite, the
randomised campaign, the secret scan and the anchor check, and it also reads the numbers
the README prints and refuses if any of them has drifted from reality. Claim drift is a
real failure mode here — a stale count in a document is the same class of problem as a
stale count in a metric, and twice during this build a hand-copied number went wrong.

Exit codes: 0 all gates passed · 1 a gate failed · 2 the environment is wrong (bad cwd).
"""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PLACEHOLDER = "PASTE_VIDEO_LINK_HERE"
