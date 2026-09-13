# Architecture

## Module map

```
src/causal/
  intent.py          contract, canonical hashing, evidence gate, proposal validation
  policy.py          every dangerous transition lives here and nowhere else
  registry.py        conflict keys, partial unique index, supersede / resume policy
  ledger.py          12-state effect machine with enforced legal transitions
  reconcile.py       EXACT / LIKELY / AMBIGUOUS / NOT_FOUND → a deterministic action
  postconditions.py  per-app pre-registered checks, including temporal claims
  audit.py           hash-chained append-only log, tamper-evident
  engine.py          the orchestrator; the fixed order lives in `run`
  apps.py            the in-process services + the protocols the engine programs against
  adapters.py        live Gmail / Calendar / Linear / Slack clients (LIVE and TWIN)
  evidence_sink.py   outcome records and the aggregate metrics
```

The engine depends only on the protocols in `apps.py`. `LOCAL`, `LIVE` and `TWIN` all satisfy
the same four interfaces, which is why switching between them is one environment variable and
zero engine changes.

## The order, and why it is that order

```
1. expiry            refuse before doing anything at all
2. evidence gate     no approval → no effects, zero writes
3. authority check   the model may not move the disputed fact
4. freeze            hash the contract and the authority map; make the map immutable
5. scope check       every effect, before any write
6. conflict claim    atomic; one active intent per business outcome
7. plan              effects enter PLANNED
8. execute           PLANNED → REQUESTED → EFFECTED | UNKNOWN
9. verify            a different call than the writer; postconditions decide
10. reconcile        UNKNOWN is settled by reading, never by retrying
11. commit gate      can_commit() — code only
```

Steps 3 and 5 come before step 6 deliberately: an intent with a bad scope should not even
claim the outcome, let alone hold it. Step 4 precedes everything that writes because a
frozen authority is worthless if anything has already acted.

## The effect state machine

```
PLANNED ──► REQUESTED ──► EFFECTED ──► VERIFYING ──► VERIFIED
                │                          │
                │                          └──► VERIFICATION_FAILED ──► VERIFYING
                └──► UNKNOWN ──► RECONCILING ──┬─► VERIFIED
                                               ├─► NOT_FOUND ──► REQUESTED
                                               ├─► AMBIGUOUS ──► ESCALATED
                                               └─► UNKNOWN   (the read itself failed)
```

Enforced by a transition table. `PLANNED → VERIFIED` raises `IllegalTransition`. So does
`VERIFIED → REQUESTED`; leaving `VERIFIED` requires the explicit, audited
`invalidate_verified(reason)`.

`attempts` counts **write attempts only**, so `attempts == 1` next to a `VERIFIED` effect is
positive evidence that reconciliation avoided a duplicate rather than that a retry was skipped.

## Three decisions worth defending

**The conflict key excludes the disputed fact.** `CUSTOMER::PROJECT::EVENT`, no time. If the
time were part of the key, two intents disagreeing about the time would produce two different
keys and never collide — the conflict detector would be dead code that always reports CLEAR.
The disputed value belongs in the scope, where the authority map adjudicates it.

**The scope authorizes; the payload is what gets written.** An effect carries a payload that
the model proposed. The scope decides whether the effect is permitted at all; the payload
decides what the artifact actually contains. Keeping them separate is what makes "the API
succeeded and the action failed" expressible instead of a contradiction.

**Reads are never skipped on a resume; only effects are.** An earlier version of this code
skipped an item of evidence because its step was already checkpointed, lost the approval, and
refused a legitimate refund. Reads are idempotent and always re-read. Effects are checkpointed,
claimed, and skipped.

## The app boundary

| concern | owner |
| --- | --- |
| whether an effect may happen | `policy.can_execute` |
| whether an effect already happened | `ledger` + `reconcile` |
| what the artifact says | `postconditions` |
| whether the intent may commit | `policy.can_commit` |
| carrying it out | `adapters` |

Adapters carry out decisions. They never make them: there is no authorization check anywhere
outside `policy.py`, and if one appears there, that is a bug.
