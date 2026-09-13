#!/usr/bin/env python
"""Walk DEMO.md's click order against a running console and check every number it quotes.

A demo script is a claim about what is on screen. This turns that claim into something
checkable, because the alternative is discovering mid-recording that a button moved or a
count drifted.

    uv run uvicorn causal.api:app --port 8000 &      # in one shell
    uv run python scripts/demo_preflight.py          # in another

Exits non-zero if any quoted value does not match, printing the real value beside the
claimed one. It resets the ledger first, because the script says to.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

FAILURES: list[str] = []
PASSES = 0

#: Strings DEMO.md tells the recorder to point at. They must exist in the page that is
#: served, because a script written from the API payload names payload keys (`events_matching`)
#: while the page paints its own labels (`matching events`) — and a recorder cannot find a
#: phrase that is not on screen. Checked case-insensitively against the served HTML.
CONSOLE_LABELS = (
    "reset ledger", "Ask in words", "read it",
    "a proposal that widens its own authority",
    "wrote this run", "read back from apps", "matching events", "worker A's effect",
    "while the lease was live", "matching candidates",
    "Effects · write path vs independent read", "Commit gate",
    "rows unedited", "whole-log chain linked",
    "Invariant counters", "Audit chain",
)
REVIEW_LABELS = (
    # static markup only: the job headlines are composed at runtime by plain.job_view and
    # are checked against the API payload below instead, where they actually come from
    "Approve and send", "operator console", "in words", "show the states",
)


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return {"http_error": exc.code, "detail": exc.read().decode()[:200]}
    except Exception as exc:                                    # noqa: BLE001
        print(f"\n  cannot reach the console at {url}: {exc}")
        print("  start it with: uv run uvicorn causal.api:app --port 8000")
        raise SystemExit(2)


def check(label: str, got, want) -> None:
    global PASSES
    if got == want:
        PASSES += 1
        print(f"  ok    {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          script says: {want!r}\n          console says: {got!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    b = args.base.rstrip("/")

    print("DEMO.md preflight — walking the click order\n")

    print("the buttons the script names:")
    state = call("GET", f"{b}/api/state")
    titles = [s["title"] for s in state.get("sequences", [])]
    for label in ("Worker A dies holding the job — worker B takes over, no duplicate",
                  "An equivalent effect already exists — refuses to commit",
                  "Outbound waits for a person; internal effects do not",
                  "The API succeeds and the action still fails"):
        check(f"button exists: {label[:44]}…", label in titles, True)

    print("\npress reset ledger:")
    check("ledger reset", call("POST", f"{b}/api/reset").get("ok"), True)

    print("\nthe labels the script tells you to point at:")
    with urllib.request.urlopen(f"{b}/", timeout=30) as resp:
        console_html = resp.read().decode()
    with urllib.request.urlopen(f"{b}/review", timeout=30) as resp:
        review_html = resp.read().decode()
    for label in CONSOLE_LABELS:
        check(f"console shows: {label[:46]}", label.lower() in console_html.lower(), True)
    for label in REVIEW_LABELS:
        check(f"review shows:  {label[:46]}", label.lower() in review_html.lower(), True)

    print("\n0:10  click crash_recovery:")
    r = call("POST", f"{b}/api/run/crash_recovery")
    check("wrote this run calendar = 0", r["wrote"]["calendar"], 0)
    check("events_matching = 1", r.get("events_matching"), 1)
    check("truth calendar = 1", r["truth"]["calendar"], 1)
    check("status COMMITTED", r["result"]["status"], "COMMITTED")
    check("rows unedited (per intent)", r.get("audit_intact"), True)
    check("whole-log chain linked", r.get("chain_linked"), True)

    print("\n0:35  click duplicate_before_commit:")
    r = call("POST", f"{b}/api/run/duplicate_before_commit")
    check("candidates = 3", r.get("candidates"), 3)
    check("CALENDAR-01 AMBIGUOUS", r["effect_states"]["CALENDAR-01"], "AMBIGUOUS")
    check("not committed", r["result"]["committed"], False)

    print("\n0:55  click awaiting_signoff:")
    r = call("POST", f"{b}/api/run/awaiting_signoff")
    check("CALENDAR-01 VERIFIED", r["effect_states"]["CALENDAR-01"], "VERIFIED")
    check("LINEAR-01 VERIFIED", r["effect_states"]["LINEAR-01"], "VERIFIED")
    check("SLACK-01 AWAITING_APPROVAL", r["effect_states"]["SLACK-01"], "AWAITING_APPROVAL")

    print("\n1:05  the review tab, then Approve and send:")
    jobs = call("GET", f"{b}/api/jobs")
    check("three jobs on the review page", len(jobs["jobs"]), 3)
    check("exactly one waiting for a person", len(jobs["need_approval"]), 1)
    check("the waiting job's headline",
          jobs["need_approval"][0]["headline"], "Waiting for your approval to send SLACK-01.")
    given = sorted(j["headline"] for j in jobs["jobs"])
    check("the stopped job says why it stopped",
          "Two identical things exist. I stopped rather than guess." in given, True)
    check("a finished job reads as finished",
          "Done. 3 of 3 systems confirmed." in given, True)
    approved = call("POST", f"{b}/api/approve",
                    {"intent_id": jobs["need_approval"][0]["intent_id"],
                     "approved_by": "dana.reyes@acme.example"})
    check("approving commits the job", approved["result"]["committed"], True)
    after = call("GET", f"{b}/api/jobs")
    check("nothing waits any more", len(after["need_approval"]), 0)
    check("its row now reads done",
          [j["headline"] for j in after["jobs"] if j["intent_id"] == jobs["need_approval"][0]["intent_id"]],
          ["Done. 3 of 3 systems confirmed."])

    print("\n1:25  the Ask in words panel:")
    text = ("Acme approved the renewal. Schedule the renewal kickoff 2026-10-07T15:00 "
            "and open the renewal task.")
    good = call("POST", f"{b}/api/intake", {"text": text})
    check("read it -> accepted", good["accepted"], True)
    check("  conflict key", good["intent"]["conflict_key"], "ACME::RENEWAL::KICKOFF")
    check("  authority is the operator's", good["intent"]["authority"]["work"], "linear")
    bad = call("POST", f"{b}/api/intake", {"text": text, "adversarial": True})
    check("widen-authority -> refused", bad["accepted"], False)
    check("  refused on two fields", len(bad["reasons"]), 2)

    print("\n1:45  the counters:")
    metrics = call("GET", f"{b}/api/state")["metrics"]
    check("three jobs", metrics["intents"], 3)
    check("two committed", metrics["committed"], 2)
    check("zero false commits", metrics["false_commits"], 0)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} claim(s) in DEMO.md no longer match the console:")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print(f"{PASSES} claims checked, all match. The click order is good to record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
