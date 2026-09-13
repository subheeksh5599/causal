# Limitations

Written to be read by a sceptic. Everything here is a real bound on what the system can claim.

## Not claimed

- **Three apps, not a platform.** Gmail authorizes, Calendar schedules, Linear works. The
  protocol is app-agnostic and the adapter interface is small, but only these three have live
  clients. Slack is wired and is the held outbound effect in the sign-off sequence — a fourth
  surface, not a general notification system.
- **Only a `LIVE` or `TWIN` run demonstrates the integrations.** `LOCAL` mode exercises the
  protocol deterministically against in-process services. It is not evidence about Google's or
  Linear's APIs, and it is never presented as such.
- **No atomicity across providers.** Two services with no shared transaction cannot be made to
  commit together by an orchestration layer. CAUSAL aims at the honest version: detect the
  disagreement, refuse to call it success, and reconcile or escalate. That is weaker than a
  distributed transaction and it is not a distributed transaction.
- **Two genuinely concurrent callers can still race.** Where a provider offers no atomic
  idempotency key, CAUSAL narrows this to a local unique constraint plus an external read-back;
  it does not close it.

## Bounded by inputs

- **Correctness is bounded by what a provider exposes.** If an API returns materially incomplete
  state, no read-back can be better than that state.
- **The commit is only as strong as the postconditions.** Four checkers ship. They are strict —
  exact title, exact start, exact attendees, intent hash present, and a temporal-claim rule
  that refuses any artifact whose date contradicts the frozen scope — but they are also a small,
  hand-written set. A gap in a checker is a gap in the guarantee.
- **Temporal claims are matched textually.** A date expressed in a form the parser does not
  recognize will not be compared. The rule catches what it can parse; it does not understand
  language.

## Deliberate stops

- **`AMBIGUOUS` escalates to a human.** Two plausible artifacts are not resolved by choosing.
  This is a feature with a cost: the workflow stalls where a guesser would have continued.
- **`UNKNOWN` can stay unknown indefinitely** if the read that would settle it is also failing.
  The system refuses to progress rather than retry blind, which is the correct failure mode and
  an inconvenient one.

## Engineering bounds

- **Single-host durable store.** SQLite with a partial unique index and `BEGIN IMMEDIATE`. The
  concurrency guarantee is real on one machine. Horizontal scale needs the same constraint in
  Postgres; the `claim`/`checkpoint` interfaces are what would survive the move.
- **External objects can change after verification.** Detected on a subsequent read, not
  prevented. Nothing prevents a human editing the calendar event afterwards.
- **No accounts, tenancy or hosted deployment.** There is a local operator surface — the console
  and the review page, both served by the same process — but it is a tool for one operator on one
  machine, not a product with users.
- **The model is optional by design.** The protocol runs with zero model calls, which is what
  makes invariant 10 true. Extraction from natural language is the model's only intended job,
  and it is the least exercised part of the system.

## How this is tested for

`EVALUATION.md` carries the fault matrix: for each fault, the external state, the CAUSAL state,
and the behaviour that is required. Where a fault cannot be produced in `LOCAL` mode, the
matrix says so rather than implying coverage.
