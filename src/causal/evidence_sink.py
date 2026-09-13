"""Outcome store: the numbers the judge sees, computed from real runs only.

Nothing here is estimated. If a counter is zero it is because no run produced a
non-zero value, and the refusal rate is refusals divided by runs.
"""
from __future__ import annotations
import json
import sqlite3
import threading
