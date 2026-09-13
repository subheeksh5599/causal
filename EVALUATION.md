# Evaluation

## Test suite

```
$ uv run pytest tests/
151 passed in 1.7s
```

| group | file | tests | what it establishes |
| --- | --- | ---: | --- |
| intent contract + authority freeze | `test_a_contract.py` | 36 | deterministic hashing, canonicalization, immutable authority |
| evidence gate, scope/policy, conflict | `test_c_evidence_scope_conflict.py` | 42 | no evidence → no writes; no scope → no writes; one active intent per outcome |
| effect ledger, verification, reconciliation | `test_d_lifecycle.py` | 32 | legal transitions only, different-call read-back, EXACT/LIKELY/AMBIGUOUS/NOT_FOUND |
| faults, crash, adversarial model, commit gate | `test_e_fault_adversarial_commit.py` | 27 | resume without duplication, an outage never commits, a lying model changes nothing |
| end-to-end sequences | `test_smoke.py` | 10 | the six scenarios run |
| three-app flagship | `test_three_app_flagship.py` | 4 | the shipped workflow shape |

Two properties of the suite matter more than its size:

1. **Every failure test asserts two things** — the returned state, and the absence of an
   unauthorized side effect. "Refused" with a write that still happened is a failure.
2. **Counts are deltas, not totals.** Assertions compare before/after effect counts, so a test
   cannot pass because some earlier test happened to create the artifact.

## Fault matrix

| fault | external state after | CAUSAL state | required behaviour | test |
| --- | --- | --- | --- | --- |
| failure before the write | absent | `UNKNOWN` → `NOT_FOUND` | retry is allowed | 121, 125 |
| timeout after the write landed | exists | `UNKNOWN` → `RECONCILING` | reconcile, never retry | 123, 088a |
| crash after the write | exists | `UNKNOWN` at restart | a fresh process reconciles it | 124 |
| verification API unavailable | unknown | `UNKNOWN` | no commit; never a duplicate write | 129 |
| verification outage clears later | exists | resumes | next run verifies | 130 |
| wrong date in the artifact | exists | `VERIFICATION_FAILED` | no commit | 099, 142 |
| wrong title / customer / project | exists | `VERIFICATION_FAILED` | no commit | 096–098 |
| missing intent binding | exists | `VERIFICATION_FAILED` | no commit | 102 |
| object tampered with afterwards | modified | `VERIFICATION_FAILED` | no commit | 103 |
| object deleted afterwards | absent | `VERIFICATION_FAILED` | no commit | 104 |
| stale approval | irrelevant | `REFUSED: EVIDENCE_STALE` | zero writes | 037 |
| future-dated approval | irrelevant | `REFUSED: EVIDENCE_MISSING` | zero writes | 038 |
| concurrent intent, same outcome | competing | `REFUSED: CONFLICT` | zero writes from the loser | 065, 071 |
| out-of-scope action | irrelevant | `REFUSED: OUT_OF_SCOPE` | zero writes | 051, 057–059 |
| expired intent | irrelevant | `REFUSED: STALE_AUTHORIZATION` | zero writes | smoke |
| model claims universal success | unchanged | driven by real state only | `COMMITTED = false` | 136 |

Faults marked *absent* / *exists* are produced by the two injection flavours on the local
clients: before the write (nothing landed) and after the write (landed, response lost). In
`LIVE` mode the same states arise naturally from timeouts; they are not injectable on demand.

## What each row is worth

The interesting rows are the ones where the external system says yes and CAUSAL says no:

- **timeout after the write** — the only correct answer is to read before acting. Attempts stays
  at 1 and exactly one artifact exists, which is positive evidence rather than an absence of
  errors.
- **wrong date in the artifact** — the API accepted the write. The postcondition compares the
  artifact against the frozen scope and refuses. This is the case a normal agent cannot see,
  because every signal it receives says success.
- **model claims universal success** — the counter of times the engine consulted a model is
  zero. Not "the model was ignored": there is no parameter through which a model opinion could
  arrive. If a refactor introduces one, test 136 fails.

## Aggregate counters

Reported by `scripts/demo.py` from the runs it just performed — never hand-written:

```
sequences                        6
external apps                    3
required effects per intent      2
commits                          2
refusals                         3
ambiguous outcomes reconciled    1
duplicate effects prevented      2
duplicate effects written        0
false commits                    0
llm-approved completions         0
```

`duplicate effects prevented` counts two distinct mechanisms doing real work: a reconciliation
that found an existing effect instead of writing a second one, and a conflict refusal that
stopped a second intent from acting on a held outcome.

## What is not measured

- **No randomized property-based campaign over the sign-off boundary.** The 100-run
  campaign exercises concurrency, duplicates and ambiguous effects across 48 fault
  combinations, and the boundary is covered by 14 deterministic tests — but the campaign
  runs with an empty sign-off set, so the two have not been put together yet.
  `test_i_review.py` is where that boundary is argued.
- **No live latency or provider-drift data.** The live adapters have been exercised against
  Linear (write, then a read through a different operation, plus a negative control).
  `scripts/verify_live.py` proves the Google OAuth client is valid and reports the Google
  surfaces as UNCONFIGURED rather than claiming anything about them.
- **No false-commit rate over a corpus.** The count is zero over 223 tests, thirteen
  sequences and 100 randomised campaign runs. That is not the same as zero over an
  adversarial corpus, and it is not presented as if it were.
