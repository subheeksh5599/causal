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
