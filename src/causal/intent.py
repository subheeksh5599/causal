"""Intent contracts: the thing that is authorized, frozen, and later proven.

Two design decisions worth stating, because both are load-bearing:

* The conflict key is the BUSINESS OUTCOME IDENTITY ONLY (customer/project/
  event). It deliberately excludes the disputed fact — the time. If the
  disputed value were inside the key, two intents that disagree about it would
  never collide and the conflict detector would be dead code that always
  reports CLEAR.

* The intent hash is computed once, over the canonical form. Everything
  external later has to prove it belongs to that hash.
"""
from __future__ import annotations
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
class FrozenIntentError(RuntimeError):
    """Raised when anything tries to mutate an intent after it is frozen."""


# The complete set of operations this system is permitted to perform. An
# unknown operation is refused before any adapter is consulted.
ALLOWED_OPERATIONS: dict[str, set[str]] = {
    "calendar": {"CREATE_EVENT"},
    "linear": {"CREATE_ISSUE"},
    "slack": {"POST_MESSAGE"},
    "gmail": set(),
}
