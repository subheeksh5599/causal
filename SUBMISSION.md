# CAUSAL — system and reliability brief

**Intent-bound cross-app execution: commit once, prove once, never double-act.**
Multi-App AI Agent Hackathon · Sunday, September 13, 2026

---

## The problem

AI agents act on APIs that were never designed for autonomous, retrying actors. Stripe takes
an idempotency key; Gmail, Calendar and Linear do not. They never saw your intent id, they
do not participate in your transaction, and when a response is lost the writer cannot tell
whether its effect exists. A blind retry duplicates it; no retry loses it. Every dashboard
stays green either way, because nothing in the stack treats "the call returned" as different
from "the outcome happened".

## What it does

CAUSAL sits between an agent and the apps it acts on and makes one promise it can prove:
**an external effect is not done because it exists — it is done only if it is provably the
authorised, conflict-free consequence of exactly one frozen intent.**

```
EFFECTED  ≠  VERIFIED  ≠  COMMITTED
```

- **EFFECTED** — an external write appears to have occurred. This is what the writer says.
- **VERIFIED** — an independent read, through a *different call than the writer used*,
  confirms the required postcondition. This is what the system says.
- **COMMITTED** — every required effect is verified, conflict-free, causally bound to the
  same frozen authorisation, and not expired. This is what the evidence says.

A 200 response is sufficient for none of the three.

Three mechanisms carry that promise:

1. **A frozen intent contract.** A request becomes a scope, an authority map, a set of
   required effects, and a conflict key. The authority map is an immutable mapping proxy at
   freeze — no later code and no model can widen it while keeping a valid hash.
2. **Semantic effect binding.** With no idempotency key anywhere, CAUSAL computes a
   deterministic fingerprint of the intended effect and searches the surface for an
   equivalent that already exists. One match is bound and adopted, not duplicated. More than
   one match is `AMBIGUOUS` and it **refuses**, because guessing which effect is yours is
   worse than stopping.
3. **A commit gate with no partial success.** `can_commit` is the only path to `COMMITTED`
   and it reads the ledger, never a response body, a planner output or a webhook.

## The three external apps, and why each one is load-bearing

The requirement is a multi-step agent connected to at least three external apps. Three
apps, three distinct roles — none of them decoration, because the problem CAUSAL solves
only exists when effects are spread across systems that share no transaction boundary.

| App | Role in the job | What it proves |
|---|---|---|
| **Gmail** | **The authority.** The approval message authorises the whole intent, and it adjudicates the disputed fact: the start time comes from the message, not from a plan and not from a model. CAUSAL never writes to it | that authorisation is external evidence, read through a different call than the one used to find it |
| **Calendar** | **The scheduling effect.** The write that a lost response would otherwise duplicate, and the object whose ownership becomes ambiguous when two identical events exist | a write, then an independent read-back through a different endpoint, filtered on the intent's own tag |
| **Linear** | **The work effect.** The ticket the team actually acts on — **verified live**, issue created via GraphQL and found again through a *different* operation, with a negative control returning zero | that the third app is genuinely connected, not simulated |

Gmail is what makes the others safe: without an external authority for the disputed fact,
"the agent did what was approved" is an assertion. The disagreement between a message's
date and a plan's date is the whole reason the evidence gate exists, and it cannot be
demonstrated inside one app.

## What is verified, and how

| Claim | Evidence |
|---|---|
| 237 tests pass in ~3.7s | `uv run pytest tests/` — twelve files, per-file counts in the README |
| 12-state effect machine, illegal transitions refused | `PLANNED → VERIFIED` raises; leaving `VERIFIED` needs an audited invalidation |
| The write response is never trusted | a test lies on the write (`"WRONG-ID-FROM-THE-WRITE-RESPONSE"`) and the effect still verifies to the *real* object |
| The model cannot cause a transition | the decision modules import no model client; a test reads their imports to enforce it, and the console reports the count as an aggregate over persisted per-run counters |
| Semantic recovery 100% / false binding 0% | measured over a seeded suite in `tests/test_g_binding.py`, including near-misses differing only by a day |
| 100 randomised adversarial runs, 48 fault combinations, 0 invariant violations | `scripts/campaign.py`; artifact in `evidence/campaign.json` |
| Tamper-evident audit | editing any row fails both the per-intent and whole-log hash-chain check |
| Linear is genuinely wired | issue created via GraphQL, then found through a *different* operation, plus a negative control that returns zero |

**The reliability posture is refusal, not optimism.** Where absence of an effect cannot be
proven — a surface with no way to enumerate its domain — CAUSAL returns `NOT_PROVABLE` and
does not retry. A duplicate appearing after a legitimate commit is flagged and escalated,
never silently reversed. `AMBIGUOUS` stops and asks a human. There is no
`COMMITTED_WITH_WARNINGS`.

## What is not claimed

- **Not a distributed transaction.** Two providers with no shared transaction cannot be made
  to commit together. CAUSAL detects the disagreement and refuses to call it success. That is
  weaker than atomicity and is not presented as atomicity.
- **Gmail and Calendar are not live-exercised.** Their endpoints, headers and bodies are
  audited against Google's own contracts and the write/read tag agreement is proven offline
  (`tests/test_j_live_adapters.py`), and `scripts/live_run.py` drives the whole flagship
  against the real apps on demand. What is missing is one browser consent, not code — so in
  a `LOCAL` run those two surfaces are in-process services and the badge says so.
- **The store is single-host SQLite.** The uniqueness guarantee is a partial unique index in
  the database, real and single-machine.
- **The demo clock's fixtures are seeded** with fixed approvals, because a deterministic
  demonstration needs fixed inputs. The dates themselves are computed from the real clock.

## Deliverables

- **Repository:** see the submission form. One command, no build step:
  `uv sync && uv run pytest && uv run uvicorn causal.api:app --port 8000`
- **Two-minute demo:** linked at the top of the README. Six clicks, walked in `DEMO.md`,
  and `scripts/demo_preflight.py` checks all 31 numbers the script quotes against the
  running console before recording.
- **This brief:** the system above, and the reliability story, in one page.
- **One command that checks the other three:** `uv run python scripts/verify_all.py` runs
  the suite, the randomised campaign, the secret scan and the anchor check, and reads the
  numbers printed in the README back against reality — failing if any of them has drifted.

---

### Paste-ready short description

CAUSAL gives AI agents idempotent commit semantics over APIs that provide no idempotency
primitive — Gmail, Calendar and Linear — by binding an intent to an independently observed
effect instead of trusting a response. It freezes an intent contract, executes against it,
reads every effect back through a *different* call than the one that wrote it, proves each
effect belongs to exactly one intent by semantic fingerprint, and refuses to commit when
ownership cannot be proven. 237 tests, a 100-run randomised adversarial campaign with zero
invariant violations, and a measured 0% false-binding rate. Built as a deterministic Python
protocol with a single-command console: three external apps, one frozen intent, and a
refusal whenever success cannot be proven.
