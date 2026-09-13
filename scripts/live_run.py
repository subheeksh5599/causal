"""Run the flagship against the three real apps: Gmail, Calendar and Linear.

    uv run python scripts/live_run.py --find-only     # search and read; writes nothing
    uv run python scripts/live_run.py                 # read, then write Calendar + Linear

This is the difference between "wired" and "used". The console in `LOCAL` mode drives
in-process services; this drives the real ones, so the three apps are genuinely connected
and the evidence is their own state.

The flow is the product, in order:

  1. SEARCH the real mailbox for candidate approvals (a different call than the read).
  2. READ each candidate and run the real evidence gate over it.
  3. Take the start time from the message that passes, because the approval is the
     authority for the disputed fact — not this script and not a model.
  4. Execute: Calendar write, then Linear write, each verified by reading it back
     through a different operation than the one that wrote it.

Google does not email attendees for an insert unless `sendUpdates` is set, and this does
not set it, so the calendar event notifies nobody. No credential is ever printed.
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
