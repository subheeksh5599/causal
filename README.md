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

## Architecture

```
    request (words)
         │
         ▼
   ┌─────────────┐   a proposer offers a contract; deterministic code
   │  intake.py  │   accepts or refuses it. authority, conflict key,
   └─────────────┘   postconditions and recipients are the operator's.
         │
         ▼
   ┌─────────────┐   freeze: scope + authority map + required effects
   │  intent.py  │   conflict_key = CUSTOMER::PROJECT::EVENT
   └─────────────┘   (the disputed fact is deliberately NOT in the key)
         │
         ▼
   ┌─────────────┐   one active intent per outcome, enforced by a partial
   │ registry.py │   unique index in SQLite — not check-then-act
   └─────────────┘
         │
         ▼
   ┌─────────────┐   PLANNED ──▶ REQUESTED ──▶ EFFECTED ──▶ VERIFYING ──▶ VERIFIED
   │  engine.py  │     │            │                        │
   └─────────────┘     │            └──▶ UNKNOWN ──▶ RECONCILING
         │             ├──▶ NOT_FOUND / NOT_PROVABLE / AMBIGUOUS
         │             ├──▶ AWAITING_APPROVAL   (outbound, waits for a person)
         │             └──▶ REJECTED / ESCALATED
         ▼
   ┌─────────────┐   gmail (authority) · calendar · linear
   │  adapters   │   read path is a different call than the write path,
   └─────────────┘   in every adapter, structurally
         │
         ▼
   ┌─────────────┐   COMMITTED = authorized ∧ frozen ∧ preconditions satisfied
   │ policy.py   │             ∧ no active conflict ∧ every required effect
   └─────────────┘               VERIFIED ∧ causally bound ∧ not expired
```

| Module | Responsibility |
|---|---|
| `intent.py` | The contract: scope, authority mapping, required effects, canonical hashing, freeze |
| `intake.py` | Natural language to contract; reserved fields a proposer may not set |
| `policy.py` | Scope and operation policy, retry decisions, and `can_commit` — the only path to COMMITTED |
| `registry.py` | One active intent per outcome, leases, takeover |
| `ledger.py` | The effect state machine and the approvals table |
| `binding.py` | Semantic fingerprints, the match hierarchy, the exclusivity relation |
| `reconcile.py` | Deterministic reconciliation levels; `AMBIGUOUS` escalates |
| `postconditions.py` | The registered checkers a required effect must satisfy |
| `audit.py` | Hash-chained event log |
| `plain.py` | The same states in words, for the review page |
| `adapters.py` | Local fakes, live clients and twin clients behind one interface |

`ARCHITECTURE.md` has the module map in full; `EVALUATION.md` has the fault matrix — for each fault, the external state, the CAUSAL state, and the behaviour that is required.

## The commit loop, step by step

The order is fixed and it is the product:

1. **Expiry** — an intent whose authorisation window has passed is refused before anything else happens.
2. **Evidence gate** — the approval message is fetched and adjudicated by deterministic checks. The model is not consulted.
3. **Authority check** — every app the intent will touch must appear in the frozen authority mapping.
4. **Freeze** — the contract is hashed. After this point the authority map is a mapping proxy and `set_authority` raises.
5. **Scope check** — out-of-scope targets and unknown operations are refused before any adapter is consulted.
6. **Conflict claim** — the conflict key is registered. A second intent on a live outcome is refused, not queued.
7. **Plan** — the required effects are enumerated with their checkers. A required effect with no registered checker is refused rather than passed.
8. **Execute** — each effect is written once. `UNKNOWN` (written, response lost) never retries; it reconciles.
9. **Bind** — the effect must be searchable back out of the surface and belong to exactly one candidate. More than one candidate is `AMBIGUOUS`, and `AMBIGUOUS` refuses.
10. **Independent verify** — a different call than the writer used reads the object back and checks the registered postcondition.
11. **Commit gate** — `can_commit` is the only path to `COMMITTED`:

```
COMMITTED = authorized ∧ authority frozen ∧ preconditions satisfied
          ∧ no active conflict ∧ every required effect VERIFIED
          ∧ every effect causally bound to the intent ∧ not expired
```

There is no `COMMITTED_WITH_WARNINGS`. Either the required outcome is proven or the intent is refused and says why. An outbound effect that reaches the outside world is held in `AWAITING_APPROVAL` until a named human approves it — and the commit gate then refuses the intent, because a required effect is not verified.

## The three apps

Three surfaces, chosen because they share no transaction boundary with each other or with CAUSAL.

| App | Role | Read path vs write path |
|---|---|---|
| **Gmail** | The authority. The approval message is the evidence that authorises the whole intent, and it also adjudicates the disputed fact — a date in the message beats a date in a model's plan | `read_message` / `list_messages` versus nothing: CAUSAL never writes to the authority |
| **Calendar** | The scheduling effect, and the one that carries the disputed fact | `insert_event` versus `list_events` — different endpoint, and objects carry the intent's tag |
| **Linear** | The work effect, and the only surface verified live | `create_task` versus `list_tasks` — a different GraphQL operation, filtered on the intent's hash |

**Linear is really wired.** Issue `SUB-5` was created through `issueCreate`, found again through a `filter: description contains` query — a different operation — and a hash that was never written returns zero, so the read is not simply returning everything. That is the difference between an adapter and a demo.

Gmail and Calendar are written against documented request shapes. Their endpoints, headers
and bodies have been audited against Google's own contracts, and `scripts/live_run.py`
drives the whole flagship against the real three apps — search the mailbox, read the
approval, take the start time the approval states, then write Calendar and Linear and read
each back. It refuses cleanly and writes nothing until the consent exists, so the only
missing step is a browser click. `scripts/verify_live.py` proves the OAuth client is valid
and reports the surfaces as UNCONFIGURED rather than implying coverage. The environment
badge says `LOCAL` because that is the truth.

## Where the guarantee is enforced

Three places, and none of them is a convention.

- **The state machine.** Twelve states, legal transitions only. `PLANNED → VERIFIED` raises rather than silently succeeding, and leaving `VERIFIED` requires an audited invalidation. The suite checks the count is exactly twelve and that a model claiming success cannot cause any transition at all.
- **The commit gate.** `policy.can_commit` is a pure function over the ledger's state. It never reads a response body, a planner output or a webhook, because it is not given one.
- **The audit chain.** Every state change is appended to a hash-chained log, so the record of what happened cannot be edited without detection. Verified by editing a row and watching both checks fail:

```
intact, per-intent: True      whole log: True
after editing a row, per-intent: False      whole log: False
```

## What the binding layer measures

The claim is not "we find something similar", it is "we can prove which external effect is ours, and we refuse when we cannot". Four rates, computed in `tests/test_g_binding.py` over a seeded suite rather than asserted in prose:

```
semantic recovery      100%   of crash-after-write cases where the effect exists
                              but carries no tag of ours — bound, not duplicated
false binding            0%   on near-misses sharing customer, project, title and
                              attendees, differing only by a day. A false bind is
                              the worst outcome available: it commits an intent
                              whose effect never happened.
duplicate prevention   100%   two workers, one real-world effect
ambiguity refusal      100%   two equivalent candidates, never guessed between
```

And the randomised campaign: **100 runs, 48 fault combinations, zero invariant violations**, including invariants for bindable effects, ambiguous effects, and the ban on retrying anything that ended `NOT_PROVABLE`.

`scripts/campaign.py` injects faults (lost responses, duplicates, foreign objects, ambiguous pairs) across random customers, projects and times, then asserts the invariants per run. The artifact is `evidence/campaign.json`.

## Where the model sits

Two places, and neither is the decision path.

**Intake.** A request in words becomes a frozen contract. A proposer reads the sentence and offers a proposal; deterministic validation accepts or refuses it. The proposer can be a model (`CAUSAL_MODEL_URL` + `CAUSAL_MODEL_KEY`) or the shipped rule parser, and swapping them cannot widen what the system accepts — that is a test, not a hope.

```
$ curl -s localhost:8000/api/intake -d '{"text":"Acme approved the renewal. Schedule
  the renewal kickoff 2026-10-07T15:00 and open the renewal task.","adversarial":true}' \
  -H 'content-type: application/json' | jq .reasons
[
  "proposal sets 'authority': the authority mapping is the operator's, so a proposal may not set it",
  "proposal sets 'recipients': who gets notified is an operator decision, never a proposer's"
]
```

Six fields are reserved: `authority`, `conflict_key`, `postconditions`, `intent_hash`, `status`, `recipients`. A proposal that reaches for any of them is refused on the field. A model that reroutes which system adjudicates the work, or widens who gets contacted, fails at the boundary rather than at review.

**Diagnosis.** Nowhere. The engine has no model call site at all, which `test_f_campaign.py::test_no_model_client_is_imported_in_the_decision_path` enforces by reading the imports of the five decision modules. The `lying_model` sequence attaches a counting planner to the engine and reports its call count, so "commits decided by a model: 0" is an aggregate over persisted per-run counters rather than a number written into the metrics function — and a test consults the hook by hand to prove the counter can leave zero.

