"""The counters panel is a claim about the world, so it has to survive being pressed twice.

Found by a real click sequence: run `awaiting_signoff`, approve it in the review page, then
press the same sequence button again — which is a natural thing to do when demonstrating the
approval, because the button is the one that created the waiting job. The panel's `committed`
count went DOWN, from 2 to 1, while the ledger still held two committed intents. The
narration reads out loud that these counters are "computed by walking every commit and
re-reading the ledger", and a counter that contradicts the ledger makes that sentence false.

The cause was in the store rather than the engine: the outcome table is keyed one row per
intent and written with INSERT OR REPLACE, so the second press recorded its IDEMPOTENT result
over the intent's committed one. Nothing about the work changed; only the record of it did.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

WALK = ("crash_recovery", "duplicate_before_commit", "awaiting_signoff")


def _state(client) -> dict:
    return client.get("/api/state").json()["metrics"]


def _walk(client) -> None:
    for name in WALK:
        client.post(f"/api/run/{name}")
    waiting = client.get("/api/jobs").json()["need_approval"]
    assert len(waiting) == 1
    client.post("/api/approve", json={"intent_id": waiting[0]["intent_id"],
                                      "approved_by": "dana.reyes@acme.example"})


def test_a_second_press_does_not_unreport_a_commit(client):
    """Pressing a sequence again after it committed must not lower the committed count."""
    _walk(client)
    before = _state(client)
    assert before["intents"] == 3
    assert before["committed"] == 2

    again = client.post("/api/run/awaiting_signoff").json()
    assert again["result"]["status"] == "IDEMPOTENT"    # the engine is right: it is done
    assert again["wrote"] == {"calendar": 0, "linear": 0, "slack": 0}

    after = _state(client)
    assert after["committed"] == before["committed"], (
        "the ledger still holds this commit; the panel must not report fewer than it holds: "
        f"{before['committed']} became {after['committed']}")
    assert after["intents"] == before["intents"]


def test_the_counters_agree_with_the_ledger(client, stack):
    """Every state counter is read off the registry, not off the last run per intent."""
    _walk(client)
    client.post("/api/run/awaiting_signoff")
    client.post("/api/run/duplicate_before_commit")
    rows = stack.registry.list_all()
    committed = sum(1 for r in rows if r["status"] == "COMMITTED")
    metrics = _state(client)
    assert metrics["intents"] == len(rows)
    assert metrics["committed"] == committed == 2


def test_a_refusal_is_not_erased_by_a_later_idempotent_run(client):
    """The same trap as above, on the refusal side: a refusal that happened still happened."""
    client.post("/api/run/conflict")
    refused = _state(client)["refused"]
    assert refused == 1
    client.post("/api/run/conflict")                    # second press
    assert _state(client)["refused"] == refused


@pytest.fixture
def client(stack, tmp_path, monkeypatch):
    """A console client on the SAME database the stack fixture made.

    The point of these tests is that two independent readers agree — the HTTP surface and a
    direct registry query. Pointing them at different files would prove nothing.
    """
    import importlib

    monkeypatch.setenv("CAUSAL_DB", stack.path)
    import causal.api as api
    importlib.reload(api)
    with TestClient(api.app) as c:
        yield c
