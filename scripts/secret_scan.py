"""Refuse to publish anything that contains a live credential.

This runs three ways:
  * manually          - uv run python scripts/secret_scan.py
  * automatically     - as .git/hooks/pre-push, so a push physically cannot
                        proceed while a secret is present
  * in CI             - as a hard gate

It checks, in order:
  1. every secret VALUE currently in .env, against the working tree
  2. the same values against every tracked file
  3. the same values against the entire git history (all commits, all branches)
  4. known credential SHAPES, so a key added later is caught even before it
     is in .env
  5. that .env is gitignored and untracked

Exit code 0 means clean. Anything else means do not push.

Nothing here ever prints a secret. Matches are reported as file:line plus a
masked fingerprint (first 6 chars, length).
"""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

# shapes that indicate a credential regardless of what is in .env
PATTERNS = {
    "arga key": re.compile(r"arga_sk_[A-Za-z0-9_\-]{10,}"),
    "google oauth secret": re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}"),
    "google refresh token": re.compile(r"1//0[A-Za-z0-9_\-]{20,}"),
    "google api key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "google client id": re.compile(r"\d{10,}-[a-z0-9]{16,}\.apps\.googleusercontent\.com"),
    "slack token": re.compile(r"xox[bpoas]-[A-Za-z0-9\-]{10,}"),
    "slack webhook": re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/+]{20,}"),
    "linear key": re.compile(r"lin_api_[A-Za-z0-9]{20,}"),
    "openai key": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws key": re.compile(r"AKIA[0-9A-Z]{16}"),
}
