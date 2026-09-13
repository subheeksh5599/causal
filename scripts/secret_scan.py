#!/usr/bin/env python3
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
    "provider api key": re.compile(r"[a-z]{2,12}_sk_[A-Za-z0-9_\-]{10,}"),
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

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".twins", "evidence"}
SKIP_FILES = {".env", ".env.local"}
PATTERN_SKIP_FILES = {"secret_scan.py"}      # its own regexes would self-match


def mask(value: str) -> str:
    return f"{value[:6]}…({len(value)} chars)"


def sh(args: list[str]) -> str:
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout
    except Exception:
        return ""


def load_env_values() -> dict[str, str]:
    """Secrets only: skip anything that is obviously a URL or a non-secret setting."""
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key.endswith(("_URL", "_BASE_URL", "_HOST", "_ENDPOINT", "_REGION")):
            continue
        if val.startswith(("http://", "https://")):
            continue
        if len(val) >= 12:
            values[key] = val
    return values


def iter_worktree_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        yield path


def scan_text(text: str, label: str, hits: list[str], *, patterns: bool = True) -> None:
    """Scan one blob. `patterns=False` is used for the scanner's own source,
    whose regexes would otherwise match themselves — but literal .env values are
    still checked there, because that is exactly where a careless paste would hide."""
    if patterns:
        for i, line in enumerate(text.splitlines(), 1):
            for pattern in PATTERNS.values():
                for m in pattern.finditer(line):
                    hits.append(f"{label}:{i}  {mask(m.group(0))}")
    for name, value in load_env_values().items():
        idx = text.find(value)
        if idx != -1:
            before = text[:idx].count("\n") + 1
            hits.append(f"{label}:{before}  literal value of {name}  {mask(value)}")


def main() -> int:
    hits: list[str] = []

    # 1 + 4: working tree
    for path in iter_worktree_files():
        try:
            scan_text(path.read_text(errors="ignore"), str(path.relative_to(ROOT)), hits,
                      patterns=path.name not in PATTERN_SKIP_FILES)
        except Exception:
            continue

    # 2: tracked files, even if they are ignored now
    tracked = [f for f in sh(["git", "ls-files"]).splitlines() if f.strip()]
    for rel in tracked:
        p = ROOT / rel
        if p.exists() and p.is_file():
            scan_text(p.read_text(errors="ignore"), f"[tracked] {rel}", hits,
                      patterns=p.name not in PATTERN_SKIP_FILES)

    # 3: full history
    history = sh(["git", "log", "-p", "--all", "--no-color"])
    if history:
        scan_text(history, "[history]", hits)

    # 5: is .env ignored and untracked?
    problems: list[str] = []
    if (ROOT / ".env").exists():
        if not sh(["git", "check-ignore", ".env"]).strip():
            problems.append(".env is NOT gitignored")
        if ".env" in tracked:
            problems.append(".env IS TRACKED — remove it from the index before pushing")

    if problems:
        print("SECRET SCAN: BLOCKED")
        for p in problems:
            print(f"  - {p}")
        return 2

    if hits:
        print("SECRET SCAN: BLOCKED — credentials present in files that would be published")
        for h in hits[:40]:
            print(f"  {h}")
        if len(hits) > 40:
            print(f"  … and {len(hits) - 40} more")
        print("\nRemove them, rotate the credential, and re-run. Do not push.")
        return 1

    print(f"SECRET SCAN: clean — {len(tracked)} tracked file(s), history: "
          f"{'yes' if bool(history) else 'empty'}, .env ignored and untracked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
