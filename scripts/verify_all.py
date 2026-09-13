#!/usr/bin/env python
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


def run(args: list[str], timeout: int = 900) -> tuple[int, str]:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, gate: str, ok: bool, detail: str = "") -> None:
        self.rows.append((gate, ok, detail))
        print(f"  {'pass' if ok else 'FAIL'}  {gate:<34} {detail}")

    @property
    def failed(self) -> list[str]:
        return [g for g, ok, _ in self.rows if not ok]


def count_tests() -> tuple[int, str]:
    """How many tests exist, and whether they all pass.

    Counted from `--collect-only` rather than by scraping the human summary line: the
    summary goes missing under `-qq` (pyproject already passes `-q`), and a gate that
    silently reads zero would fail a repository that is fine.
    """
    code, out = run(["uv", "run", "pytest", "tests/"])
    _, collected = run(["uv", "run", "pytest", "tests/", "--collect-only", "-q"])
    per_file = [int(n) for n in re.findall(r"^tests/[\w_]+\.py: (\d+)$", collected, re.M)]
    return sum(per_file), out if code != 0 else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip the 100-run campaign")
    args = ap.parse_args()

    if not README.exists():
        print(f"run this from the repository root (no README.md in {ROOT})")
        return 2

    md = README.read_text()
    report = Report()
    print("verifying the claims in README.md\n")

    # 1. the suite
    actual_tests, failure = count_tests()
    report.add("test suite passes", not failure and actual_tests > 0,
               f"{actual_tests} tests" if not failure else failure.strip().splitlines()[-1][:70])

    # 2. the numbers the README prints must equal the numbers that are true
    badge = re.search(r"tests-(\d+)%20passing", md)
    report.add("badge count is real", bool(badge) and int(badge.group(1)) == actual_tests,
               f"badge says {badge.group(1) if badge else '?'}, suite says {actual_tests}")
    summary = re.search(r"^(\d+) passed(?:, (\d+) skipped)?,", md, re.M)
    if summary:
        # A skipped test is part of the suite, so the quoted line is compared as
        # passed + skipped. Comparing only `passed` made an honest skip look like a stale
        # number, which is the opposite of what this gate is for.
        quoted = int(summary.group(1)) + int(summary.group(2) or 0)
        detail = f"README says {summary.group(1)} passed" + (
            f" + {summary.group(2)} skipped" if summary.group(2) else "")
    else:
        quoted, detail = None, "no quoted pytest line found"
    report.add("quoted pytest line is real", quoted == actual_tests, detail)
    # the per-file table must add up to the same total
    files = re.findall(r"^tests/[\w_]+\.py\s+(\d+)$", md, re.M)
    total = sum(int(n) for n in files)
    report.add("per-file table adds up", bool(files) and total == actual_tests,
               f"{len(files)} rows summing to {total}")

    # 3. the campaign
    if args.quick:
        print("  skip  randomised campaign              (--quick)")
    else:
        code, out = run(["uv", "run", "python", "scripts/campaign.py", "--runs", "100"], timeout=1800)
        clean = "no invariant violation" in out
        report.add("campaign: zero violations", code == 0 and clean,
                   "100 runs" if clean else "a run violated an invariant")

    # 4. no credential anywhere it could be pushed
    code, out = run(["uv", "run", "python", "scripts/secret_scan.py"])
    report.add("secret scan clean", code == 0 and "clean" in out,
               out.strip().splitlines()[-1][:60] if out.strip() else "no output")

    # 5. the README's own navigation works
    code, out = run(["uv", "run", "python", "scripts/readme_toc.py", "--check"])
    report.add("README anchors resolve",
               code == 0 and "every anchor resolves" in out,
               "all anchors" if code == 0 else (out.strip().splitlines()[-1][:60] or "no output"))

    # 6. the demo video is the one deliverable a script cannot produce, so at least prove
    #    the README is not about to be submitted with an empty slot in it
    report.add("demo video link is present", PLACEHOLDER not in md,
               "unfilled placeholder" if PLACEHOLDER in md else "filled")

    print()
    if report.failed:
        print(f"{len(report.failed)} gate(s) failed: {', '.join(report.failed)}")
        return 1
    print(f"all {len(report.rows)} gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
