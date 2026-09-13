#!/usr/bin/env python
"""Build the README table of contents, and verify every anchor in it resolves.

GitHub's heading slugger is not `text.lower().replace(' ', '-')`. The rules that
actually bite, all confirmed against a live README:

  * an em dash is REMOVED, so the spaces around it collapse into a DOUBLE hyphen
    (`A — b` -> `#a--b`)
  * `&` and apostrophes are dropped WITHOUT replacement, which does the same thing
    (`decisions & the hard problems` -> `#engineering-decisions--the-hard-problems`)
  * a leading `▶` is stripped but the space after it survives (`## ▶ Demo` -> `#-demo`)
  * underscores survive, other punctuation does not

So this file does not hardcode a TOC. It derives one from the headings and then
diffs the derived anchors against every inline link in the document, which is the
only way to know the nav links are not dead.

    uv run python scripts/readme_toc.py           # rewrite the TOC block in place
    uv run python scripts/readme_toc.py --check   # verify only, non-zero on a mismatch
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
MARKER = "<!--TOC-->"


def slug(heading: str) -> str:
    """GitHub's anchor for a heading. Order matters: strip punctuation first."""
    text = heading.strip().lower()
    for ch in ("—", "–"):                 # em/en dash: removed, leaving the spaces
        text = text.replace(ch, "")
    for ch in ("&", "'", "’", "`", '"', ":", ".", ",", "(", ")", "?", "!", "/", "+"):
        text = text.replace(ch, "")       # dropped without replacement
    text = text.replace("▶", "")          # stripped, leaving the space it preceded
    text = re.sub(r"[^\w\s-]", "", text)  # anything else non-word goes
    # No strip() here: GitHub replaces spaces with hyphens without trimming first, which
    # is why "## ▶ Demo" anchors as "#-demo" (it is also in the reference README's TOC).
    text = text.replace(" ", "-")
    return re.sub(r"-{3,}", "--", text)


def headings(md: str) -> list[tuple[int, str]]:
    out = []
    for line in md.splitlines():
        m = re.match(r"^(#{2,3})\s+(.*?)\s*$", line)
        if m and not line.startswith("<!--"):
            out.append((len(m.group(1)), m.group(2)))
    return out


def build(md: str) -> str:
    lines = [f"- [{text}](#{slug(text)})" for level, text in headings(md) if level == 2]
    return "\n".join(lines)


def links(md: str) -> list[str]:
    """Every in-document anchor used at all: TOC entries and inline nav links."""
    return re.findall(r"\]\(#([^)]+)\)", md)


def replace_toc(md: str, toc: str) -> str | None:
    """Rewrite the block between the TOC heading and the next rule.

    Idempotent on purpose: the first version replaced a one-shot marker, so it could
    only ever run once and every later heading change left the TOC stale.
    """
    lines = md.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## Table of contents")
    except StopIteration:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == "---"),
               None)
    if end is None:
        return None
    return "\n".join(lines[:start + 1] + [""] + toc.splitlines() + lines[end:]) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    md = README.read_text()
    anchors = {slug(text) for _, text in headings(md)}
    toc = build(md)

    if args.check:
        used = set(links(md))
        dead = sorted(a for a in used if a not in anchors)
        print(f"headings: {len(anchors)}   anchors used: {len(used)}")
        for anchor in dead:
            print(f"  DEAD: #{anchor}")
        if dead:
            print("fix the link or the heading that was supposed to match it")
            return 1
        # and the table itself must not have drifted from the headings
        current = "\n".join(
            line for line in md.split("## Table of contents")[1].split("---")[0].splitlines()
            if line.strip().startswith("- ["))
        if current.strip() != toc.strip():
            print("the table of contents no longer matches the headings — re-run without --check")
            return 1
        print("every anchor resolves, and the table matches the headings")
        return 0

    updated = replace_toc(md, toc)
    if updated is None:
        print("no '## Table of contents' heading in README.md")
        return 1
    README.write_text(updated)
    print(f"wrote {len(toc.splitlines())} TOC entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
