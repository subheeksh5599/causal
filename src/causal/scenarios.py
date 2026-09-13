"""The sequences, as callable functions.

The CLI harness (`scripts/demo.py`) and the console API (`causal/api.py`) both
drive these, so what a judge clicks is the same code path the tests and the
harness exercise. One implementation, three ways to reach it.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from . import audit as A
from . import binding
