"""Walk DEMO.md's click order against a running console and check every number it quotes.

A demo script is a claim about what is on screen. This turns that claim into something
checkable, because the alternative is discovering mid-recording that a button moved or a
count drifted.

    uv run uvicorn causal.api:app --port 8000 &      # in one shell
    uv run python scripts/demo_preflight.py          # in another

Exits non-zero if any quoted value does not match, printing the real value beside the
claimed one. It resets the ledger first, because the script says to.
"""
from __future__ import annotations
import argparse
import json
import sys
import urllib.error
