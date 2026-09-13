<div align="center">

&nbsp;

[![Tests](https://img.shields.io/badge/tests-237%20passing-10b981)](#tests)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Surfaces](https://img.shields.io/badge/surfaces-Gmail%20%2B%20Calendar%20%2B%20Linear-4DA2FF)](#the-three-apps)
[![Campaign](https://img.shields.io/badge/campaign-100%20runs%20%2F%200%20violations-2563eb)](#what-the-binding-layer-measures)
![Stack](https://img.shields.io/badge/Python%203.13%20%2B%20FastAPI%20%2B%20SQLite-1f1f23)

### Intent-bound cross-app execution — commit once, prove once, never double-act.

Most multi-app agents answer the easy question: _did the API call succeed?_ CAUSAL answers the harder one — **can you prove that each external effect is the authorised, conflict-free consequence of the one intent you approved?** It gives agents idempotent commit semantics over APIs that provide no idempotency primitive, by binding an intent to an independently observed effect rather than trusting a response. Stripe takes an idempotency key. Gmail, Calendar and Linear do not — they never saw your intent id, they do not participate in your transaction, and when a response is lost there the only question left is not _did my request succeed_ but _does an effect matching this intent already exist out there_. When nobody can prove which effect is ours, CAUSAL refuses to commit and says so.

```
EFFECTED  ≠  VERIFIED  ≠  COMMITTED
```

**[ ▶ Watch the two-minute demo ↓ ](#-demo)** &nbsp;·&nbsp; **[ Architecture ↗ ](#architecture)** &nbsp;·&nbsp; **[ The commit loop, step by step ↗ ](#the-commit-loop-step-by-step)** &nbsp;·&nbsp; **[ What's real vs pending ↗ ](#whats-real-vs-pending--the-honesty-table)** &nbsp;·&nbsp; **[ Run it locally ↗ ](#quick-start)**

</div>

---

## ▶ Demo

**→ [Watch the two-minute demo](PASTE_VIDEO_LINK_HERE)**

Two minutes, one console, six clicks. Every panel in it is filled by a real run of the same
engine the test suite drives — there is no mock state and no pre-recorded screen.

What the video shows, in order:

1. **A crash recovered without a duplicate.** Worker A writes the calendar event and dies
   before recording it. Worker B takes the job over, does **not** retry, finds A's event by
   its meaning, adopts it, and finishes only what was missing — `"calendar":0` writes, and
   exactly one event in the world.
2. **A refusal.** Two identical events exist and ownership cannot be proven, so the job
   **blocks** with an `AMBIGUOUS` chip and a candidate count, having had apparently
   successful evidence in hand.
3. **A person approving what leaves the building.** Calendar and Linear are already
   verified; the one effect that reaches the outside world waits in `AWAITING_APPROVAL`
   until somebody approves it by name.
4. **A model proposing a contract and being refused.** A request in words becomes a frozen
   contract, then the same request with a proposal that reroutes authority and widens the
   recipients is **refused on the field, by name**.
5. **The counters.** Computed by walking every commit and re-reading the ledger, not typed
   into the page.

_Recorded before submission. `scripts/verify_all.py` refuses to pass while the link above is
still unfilled, so the repository cannot ship with an empty slot in it._

---

## Table of contents

- [▶ Demo](#-demo)
- [Table of contents](#table-of-contents)
- [Quick start](#quick-start)
- [The problem I set out to solve](#the-problem-i-set-out-to-solve)
- [What I built](#what-i-built)
- [Architecture](#architecture)
- [The commit loop, step by step](#the-commit-loop-step-by-step)
- [The three apps](#the-three-apps)
- [Where the guarantee is enforced](#where-the-guarantee-is-enforced)
- [What the binding layer measures](#what-the-binding-layer-measures)
- [Where the model sits](#where-the-model-sits)
- [Who approves what](#who-approves-what)
- [Engineering decisions & the traps that taught me something](#engineering-decisions--the-traps-that-taught-me-something)
- [What's real vs pending — the honesty table](#whats-real-vs-pending--the-honesty-table)
- [The app](#the-app)
- [Prior art, credited](#prior-art-credited)
- [What this cannot do](#what-this-cannot-do)
- [Security](#security)
- [Tech stack](#tech-stack)
- [Project layout](#project-layout)
- [Full command reference](#full-command-reference)
- [How I'd deploy it](#how-id-deploy-it)
- [Results and supporting records](#results-and-supporting-records)
- [Tests](#tests)
- [License](#license)
---

## Quick start

### Requirements

- Python `3.13` and [`uv`](https://docs.astral.sh/uv/) — that is the whole list
- No Node, no bundler, no build step. The UI is two static HTML files served by the same process
- Nothing is needed in the environment to run the tests or the console; `LOCAL` mode is entirely in-process

### Install

```bash
git clone <this repository>
cd causal
uv sync
```

### Verify the repository

```bash
uv run python scripts/verify_all.py
```

That is the gate: it runs the test suite, the 100-run randomised campaign, the secret scan
and the README anchor check — and then it reads the numbers this README prints and **fails
if any of them has drifted from reality**. A stale count in a document is the same class of
problem as a stale count in a metric, and twice during this build a hand-copied number went
wrong, so the numbers here are checked rather than trusted. Use `--quick` to skip the
campaign.

A full pass looks like this:

```
verifying the claims in README.md

  pass  test suite passes                  237 passed
  pass  badge count is real                badge says 237, suite says 237
  pass  quoted pytest line is real         README says 237
  pass  per-file table adds up             12 rows summing to 237
  pass  campaign: zero violations          100 runs
  pass  secret scan clean                  SECRET SCAN: clean — 60 tracked file(s)
  pass  README anchors resolve             all anchors
  pass  demo video link is present         filled

all 8 gates passed.
```

### Run the agent

```bash
uv sync
uv run uvicorn causal.api:app --port 8000
```

Then open `http://127.0.0.1:8000` for the operator console and
`http://127.0.0.1:8000/review` for the review page. Press **reset ledger** once in the left
rail before following `DEMO.md` — the sequences share a ledger, and on an accumulated one
the same buttons honestly report different verdicts.

### Useful commands

| Command | Purpose | Touches |
|---|---|---|
| `uv run python scripts/verify_all.py` | The release gate: suite, campaign, scan, anchors, claim drift | Offline |
| `uv run pytest tests/` | 237 tests, ~3.7s | Offline |
| `uv run python scripts/demo.py` | The six original sequences, printed with narration | Offline |
| `uv run uvicorn causal.api:app --port 8000` | The console and the review page | Offline |
| `uv run python scripts/demo_preflight.py` | Walks `DEMO.md`'s click order and checks all 31 numbers it quotes | Offline, needs the console running |
| `uv run python scripts/campaign.py --runs 100` | Randomised adversarial runs with injected faults | Offline |
| `uv run python scripts/verify_live.py` | Which surfaces are actually reachable, and what is missing for the rest | Read-only HTTPS |
| `uv run python scripts/verify_live.py --write` | The same, plus a real create-and-read-back per surface | Writes to the live services |
| `uv run python scripts/live_run.py --find-only` | Search the real mailbox and adjudicate the approval | Read-only HTTPS |
| `uv run python scripts/live_run.py` | The flagship against the real three apps | Writes to Calendar and Linear |
| `uv run python scripts/secret_scan.py` | Working tree, tracked files, full git history, `.env` values | Offline |
| `uv run python scripts/readme_toc.py --check` | Proves no anchor in this file is dead | Offline |

**[Full command reference](#full-command-reference)** below has the environment variables
and the live-surface details. Everything in the table is safe to run as-is except the two
rows marked as writing.

---

## The problem I set out to solve

An ops lead approves one kickoff. She gets two calendar invites, two implementation tasks, and a Slack thread asking which one is real. Nothing errored. Every dashboard is green. The duplicate is invisible by construction, because the retry that caused it fires precisely when the audit trail is least reliable — a timeout, a restart, a redelivered webhook, or a second agent working the same request.

The second wound is quieter. A model drafts the notification for Wednesday. The approval said Tuesday. The API accepts it, the schema validates, the job goes green, and the wrong commitment is now in three systems with a success status attached to it. There is no tool in the stack whose job is to notice.

Neither failure is exotic. Both are ordinary consequences of treating "the call returned" as "the outcome happened". **The design rule this project is built on: no external effect is considered done until it has been read back from the system that owns it, and it belongs to exactly one intent.** A 200 response is sufficient for none of the three states above.

## What I built

A deterministic execution layer that sits between an agent and the apps it acts on.

1. **An intent contract that freezes before anything writes.** A request becomes a scope, an authority map, a set of required effects and a conflict key. The authority map becomes an immutable mapping proxy at freeze, so no later code — and no model — can widen it while keeping a valid hash.
2. **An evidence gate with no model in it.** The approval is adjudicated by deterministic string and regex checks: sender, phrase, customer, project, time, staleness, future-dating. The model's opinion of the approval is not consulted, ever.
3. **A twelve-state effect machine with enforced legal transitions.** `PLANNED → VERIFIED` raises. Leaving `VERIFIED` requires an audited invalidation. Every effect carries its own state, external id, write-attempt count and evidence.
4. **Independent verification and reconciliation.** The writer's response is never handed to the verifier; the read path is a different call against a different operation. When a write's fate is unknown, the system reads before it acts, and reconciles at `EXACT / LIKELY / AMBIGUOUS / NOT_FOUND` with no fuzzy matching.
5. **Semantic effect binding.** When no idempotency key exists anywhere, CAUSAL computes a deterministic fingerprint of the intended effect and searches the surface for an equivalent that already exists — then refuses when more than one matches. This is the layer that makes "commit once" true against APIs that cannot deduplicate for you.

On top of that: a conflict registry that allows one active intent per real-world outcome, a hash-chained audit log, a natural-language intake where a model may propose a contract but cannot widen what is accepted, and a review page where a person approves anything that leaves the building.

