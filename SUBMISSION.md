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

