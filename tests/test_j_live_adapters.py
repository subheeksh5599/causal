"""Group P - the live adapters, against the shapes real providers actually send.

These run without a network. The transport is stubbed so the live code path is exercised
against captured provider payloads, because the failure this file exists to prevent is
the one that only shows up under a real credential: code that is structurally correct and
still cannot work, because it was written against an imagined response.

The bug that prompted it: a real Gmail `From` header is `Name <addr@host>`, the evidence
gate compares bare addresses, so every legitimate approval would have been refused as an
unrecognised sender. Live-wired and never exercised is not the same as live-ready.
"""

from __future__ import annotations

import base64
import json

import pytest

from causal import adapters
from causal.adapters import CalendarLive, GmailLive, StaticAuth, address_of
from causal.intent import parse_approval

CONTACT = "buyer@acme.example"


@pytest.mark.parametrize("header,want", [
    ("buyer@acme.example", "buyer@acme.example"),
    ("Dana Reyes <buyer@acme.example>", "buyer@acme.example"),
    ('"Reyes, Dana" <buyer@acme.example>', "buyer@acme.example"),
    ("  Dana  <BUYER@Acme.Example>  ", "buyer@acme.example"),
    ("Dana Reyes <buyer@acme.example> (cc: someone)", "buyer@acme.example"),
    ("<buyer@acme.example>", "buyer@acme.example"),
])
def test_the_address_is_extracted_from_real_from_headers(header, want):
    assert address_of(header) == want


def test_a_display_name_cannot_impersonate_a_trusted_address():
    """The angle-bracket form wins, unconditionally."""
    spoof = '"buyer@acme.example" <attacker@evil.test>'
    assert address_of(spoof) == "attacker@evil.test"


def test_an_empty_header_is_not_a_contact():
    assert address_of("") == ""
    assert address_of(None or "") == ""


def test_a_real_gmail_message_passes_the_evidence_gate():
    """The whole point: the normalised header must satisfy the gate as written."""
    message = {"id": "18f2", "from": address_of("Dana Reyes <buyer@acme.example>"),
               "body": "Acme approved the implementation. Kickoff 2026-09-15T15:00.",
               "timestamp": 1_757_000_000.0}
    evidence = parse_approval(message, known_contacts={CONTACT},
                              expected_customer="Acme", expected_project="implementation",
                              reference_date_iso="2026-09-14", now_ts=1_757_000_000.0)
    assert evidence.ok is True, evidence.reasons
    assert evidence.stated_start_iso == "2026-09-15T15:00"


def test_a_spoofed_sender_is_refused_by_the_gate():
    message = {"id": "18f3", "from": address_of('"buyer@acme.example" <attacker@evil.test>'),
               "body": "Acme approved the implementation. Kickoff 2026-09-15T15:00.",
               "timestamp": 1_757_000_000.0}
    evidence = parse_approval(message, known_contacts={CONTACT},
                              expected_customer="Acme", expected_project="implementation",
                              reference_date_iso="2026-09-14", now_ts=1_757_000_000.0)
    assert evidence.ok is False
    assert any("attacker@evil.test" in r for r in evidence.reasons), evidence.reasons


# -- the live read path, against a captured response -----------------------

GMAIL_PAYLOAD = {
    "id": "18f2",
    "internalDate": "1757000000000",
    "payload": {
        "headers": [{"name": "From", "value": "Dana Reyes <buyer@acme.example>"},
                    {"name": "Subject", "value": "Acme kickoff"}],
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain",
             "body": {"data": base64.urlsafe_b64encode(
                 b"Acme approved the implementation. Kickoff 2026-09-15T15:00.").decode()}},
        ],
    },
}


def test_the_live_gmail_read_returns_the_shape_the_gate_consumes(monkeypatch):
    seen = {}

    def transport(method, url, *, headers, json_body=None, data=None, timeout=30.0):
        seen["url"] = url
        assert headers["Authorization"].startswith("Bearer ")
        return GMAIL_PAYLOAD

    monkeypatch.setattr(adapters, "_request", transport)
    message = GmailLive(StaticAuth("t")).read_message("18f2")

    assert seen["url"].endswith("/gmail/v1/users/me/messages/18f2")
    assert message["from"] == "buyer@acme.example"
    assert message["body"].startswith("Acme approved the implementation.")
    assert message["timestamp"] == 1_757_000_000.0
    # and it is consumable without any further massaging
    assert parse_approval(message, known_contacts={CONTACT}, expected_customer="Acme",
                          expected_project="implementation",
                          reference_date_iso="2026-09-14",
                          now_ts=1_757_000_000.0).ok is True


# -- the calendar write/read pair -----------------------------------------

def test_the_live_calendar_write_sets_the_property_the_read_filters_on(monkeypatch):
    """A write and a read that do not agree on the tag cannot verify each other."""
    calls = []

    def transport(method, url, *, headers, json_body=None, data=None, timeout=30.0):
        calls.append((method, url, json_body))
        if method == "POST":
            return {"id": "evt_1"}
        # the shape events.list really returns, including the fields the read path parses
        return {"items": [{
            "id": "evt_1", "summary": "Acme Kickoff",
            "start": {"dateTime": "2026-09-15T15:00:00+01:00"},
            "attendees": [{"email": "buyer@acme.example"}],
            "extendedProperties": {"private": {"intent_hash": "abc123"}},
        }]}

    monkeypatch.setattr(adapters, "_request", transport)
    cal = CalendarLive(StaticAuth("t"))
    cal.insert_event(title="Acme Kickoff", start_iso="2026-09-15T15:00",
                     attendees=("buyer@acme.example", "not-an-address"), intent_hash="abc123")

    method, url, body = calls[-1]
    assert method == "POST"
    assert url.endswith("/calendar/v3/calendars/primary/events")
    assert body["extendedProperties"]["private"]["intent_hash"] == "abc123"
    # an attendee field that is not an address is dropped, not sent to Google
    assert body["attendees"] == [{"email": "buyer@acme.example"}]
    assert body["start"]["dateTime"].startswith("2026-09-15T15:00")

    cal.list_events(intent_hash="abc123")
    assert "privateExtendedProperty=intent_hash%3Dabc123" in calls[-1][1]

    # and the read path parses its own response into the record the engine compares
    found = CalendarLive(StaticAuth("t")).list_events(intent_hash="abc123")
    assert len(found) == 1
    assert found[0]["intent_hash"] == "abc123"
    assert found[0]["title"] == "Acme Kickoff"
    assert found[0]["id"] == "evt_1"
    assert found[0]["start_iso"] == "2026-09-15T15:00"
    assert found[0]["attendees"] == ["buyer@acme.example"]
