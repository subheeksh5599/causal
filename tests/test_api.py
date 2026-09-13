"""The console API, exercised the way the browser exercises it.

Also asserts the thing that matters most about a console that shows a system's
state: no credential ever appears in a response.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from causal import scenarios

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "intended": "COMMITTED",
    "timeout_after_write": "COMMITTED",
    "conflict": "REFUSED",
    "unauthorized_success": "BLOCKED",
    "missing_evidence": "REFUSED",
    "lying_model": "REFUSED",
    "crash_recovery": "COMMITTED",
    "duplicate_intent": "IDEMPOTENT",
    "duplicate_before_commit": "BLOCKED",
    "duplicate_after_commit": "COMMITTED",
    "absence_not_provable": "BLOCKED",
    "counter_intent": "REFUSED",
    "awaiting_signoff": "BLOCKED",
}


def test_every_sequence_in_the_system_has_an_expected_verdict():
    """EXPECTED is a claim about each sequence. Keep it in step with the source."""
    assert list(EXPECTED) == [name for name, _ in scenarios.SEQUENCES]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CAUSAL_DB", str(tmp_path / "console.db"))
    monkeypatch.setenv("CAUSAL_MODE", "LOCAL")
    import causal.api as api

    api._state["stack"] = None
    with TestClient(api.app) as c:
        yield c
    api._state["stack"] = None


def test_health_reports_its_mode(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["mode"] == "LOCAL"
    assert body["apps"] == ["gmail", "calendar", "linear"]


def test_state_lists_every_sequence(client):
    body = client.get("/api/state").json()
    names = [s["name"] for s in body["sequences"]]
    assert names == list(EXPECTED)
    assert body["intents"] == []
    assert "metrics" in body


def test_every_sequence_reports_its_verdict(client):
    for name, verdict in EXPECTED.items():
        body = client.post(f"/api/run/{name}").json()
        assert body["result"]["status"] == verdict, f"{name}: {body['result']['status']}"
        assert body["wrote"] is not None
        assert body["chain_linked"] is True, f"{name} broke the whole-log chain"
        assert body["audit_intact"] is True, f"{name} broke its own audit rows"


def test_a_committed_run_wrote_exactly_one_artifact_per_effect(client):
    body = client.post("/api/run/intended").json()
    assert body["result"]["committed"] is True
    assert body["wrote"]["calendar"] == 1 and body["wrote"]["linear"] == 1


def test_a_refused_run_wrote_nothing(client):
    client.post("/api/run/intended")                      # so the counts have a baseline
    body = client.post("/api/run/missing_evidence").json()
    assert body["wrote"]["calendar"] == 0
    assert body["wrote"]["linear"] == 0
    assert body["result"]["refusal_code"] == "EVIDENCE_MISSING"


def test_unknown_sequence_is_404(client):
    assert client.post("/api/run/not_a_sequence").status_code == 404


def test_run_all_returns_every_sequence(client):
    body = client.post("/api/run/all").json()
    assert [r["name"] for r in body["runs"]] == list(EXPECTED)
    assert "metrics" in body


def test_reset_clears_the_ledger(client):
    client.post("/api/run/intended")
    assert client.get("/api/state").json()["intents"]
    assert client.post("/api/reset").json()["ok"] is True
    assert client.get("/api/state").json()["intents"] == []


def test_false_commits_is_computed_not_asserted(client):
    for name in EXPECTED:
        client.post(f"/api/run/{name}")
    metrics = client.get("/api/state").json()["metrics"]
    assert metrics["false_commits"] == 0
    assert metrics["commits_decided_by_a_model"] == 0
    assert metrics["duplicates_prevented"] >= 0
    assert metrics["false_commits"] == 0


def test_no_credential_appears_in_any_response(client):
    """The console shows system state. It must not show the keys behind it."""
    env = ROOT / ".env"
    if not env.exists():
        pytest.skip("no .env in this checkout")
    secrets = []
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 12:                          # ignore blanks and short values
                secrets.append((key.strip(), value))
    assert secrets, "expected at least one credential to check for"

    blobs = [client.get("/").text, client.get("/api/state").text, client.get("/api/health").text]
    for name in EXPECTED:
        blobs.append(client.post(f"/api/run/{name}").text)
    blobs.append(client.post("/api/run/all").text)

    for key, value in secrets:
        for blob in blobs:
            assert value not in blob, f"{key} leaked into a response"


def test_console_html_declares_its_mode(client):
    html = client.get("/").text
    assert "CAUSAL" in html
    assert "LOCAL" in html or "LIVE" in html or "TWIN" in html
    assert "EFFECTED" in html and "VERIFIED" in html and "COMMITTED" in html
