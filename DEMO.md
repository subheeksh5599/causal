# CAUSAL — demo script

Two minutes. Six clicks. Everything you press runs the real engine — the same code the
237-test suite and the 100-run campaign drive. No mock, no slide, no pre-filled panel.

Every number and every label below was read off the running console in exactly this click
order, on a fresh ledger. Where it says *point at*, that string is on screen.

---

## Before you record

```bash
cd causal
uv run uvicorn causal.api:app --port 8000
```

Open the console at **`http://127.0.0.1:8000/fresh`** and the review page at
`http://127.0.0.1:8000/review` in a second tab.

`/fresh` empties the ledger and lands you on the console, so it is already in the state every
number below assumes. The header reads `ledger · fresh`. **Check that before you press
record.** A plain `/` keeps the old ledger on purpose — there the header reads
`ledger · 5 intents · 1 committed` under an amber bar, and a button pressed on it answers
`IDEMPOTENT` instead of doing the work again.

Once you start, do not press **reset ledger**: the counters are real objects in the world, so
a second press reports different numbers, honestly.

Optionally prove the script still matches this build first:

```bash
uv run python scripts/demo_preflight.py
```

It walks this exact click order against the running console, checks every number quoted
below, checks that every label this file points at is really on the page, and then resets the
ledger again — so the state it hands you is the state the first click needs. It ends with
`Start recording now`.

**The recording is linked in the README already** — `## ▶ Demo`, with a poster frame and a
720p copy at `docs/media/causal-demo.mp4`. To re-record, open `/fresh` at the top of this
document's order, film the clicks, upload, and replace the two `youtu.be` links in the README
(the badge row and the demo section). Then run the release gate:

```bash
uv run python scripts/verify_all.py
```

`demo video link is present` fails if the README carries no video link at all or still carries
the `PASTE_VIDEO_LINK_HERE` placeholder, so the repository cannot be submitted with an empty
slot in it. A GitHub issue/PR upload
(`https://github.com/user-attachments/assets/...`) renders as an inline player; a YouTube
(unlisted) or Loom link also works and reads better on the submission form.

---

### 0:00 — the console, nothing clicked

> "This is an agent that touches Gmail, Calendar and Linear to finish one real job. Every
> problem it exists for starts the same way: the write succeeds and the answer gets lost. So
> there is one rule. Nothing is marked done because an API returned 200."

Point along the sentence across the top: `EFFECTED ≠ VERIFIED ≠ COMMITTED`.

> "Done means the effect was read back from the system that owns it, and it belongs to
> exactly one intent."

### 0:10 — click **Worker A dies holding the job — worker B takes over, no duplicate**

Point at the top panel, in this order — this is what it reads:

| on screen | value | say |
|---|---|---|
| `wrote this run` | `{"calendar":0,"linear":1,"slack":1}` | "Worker B wrote zero calendar events." |
| `read back from apps` | `{"calendar":1,"linear":1,"slack":1}` | "It found A's event by its meaning — this renewal, that date, those two people — and adopted it." |
| `matching events` | `1 — the world holds this many, whatever the writers claim` | "Two workers, one real event." |
| `worker A's effect` | `ext-0001 — found by its meaning, not by an id we kept` | "It never had to store A's id." |
| `while the lease was live` | `IDEMPOTENT · 0 writes · this intent already exists in state ACTIVE` | "The second worker was told to wait, not to write." |

Then the **EFFECTS** table — three rows, all `VERIFIED`:

```
CALENDAR-01   calendar   VERIFIED   ext-0001   0
LINEAR-01     linear     VERIFIED   lin-0001   1
SLACK-01      slack      VERIFIED   msg-0001   1
```

Then the **COMMIT GATE** panel:

> "And it proves the chain: `rows unedited: yes ✓ · whole-log chain linked: yes ✓` — the
> audit log is hash-chained, so the record cannot be edited without it showing."

### 0:35 — click **An equivalent effect already exists — refuses to commit**

Point at the **EFFECTS** table: `CALENDAR-01` carries the `AMBIGUOUS` chip. Then at this row
in the top panel:

```
matching candidates   3 — how many effects could be ours
```

The **COMMIT GATE** panel reads `BLOCKED` with one reason:

```
required effect CALENDAR-01 is AMBIGUOUS, not VERIFIED
```

> "Now the hard case. Three effects could be ours and the system cannot prove which. It
> refuses."

> "It had what looks like successful evidence sitting right there, and it still will not
> commit. That refusal is the product. Any system can report success; knowing when success
> cannot be proven is what makes the success worth anything."

### 0:55 — click **Outbound waits for a person; internal effects do not**

The **EFFECTS** table changes to — two confirmed, one held:

```
CALENDAR-01   calendar   VERIFIED            evt-0002   1
LINEAR-01     linear     VERIFIED            lin-0003   1
SLACK-01      slack      AWAITING_APPROVAL   —          0
```

The **COMMIT GATE** reads `BLOCKED` on `required effect SLACK-01 is AWAITING_APPROVAL, not
VERIFIED`.

> "The same intent, three effects. The calendar and the task are already confirmed. The one
> that reaches the outside world is held."

> "Two of three were done before anyone was asked. Only the notice that leaves the company
> waited — and the job is not committed while it waits."

### 1:05 — switch tabs to `/review` — the same engine in words

Three cards, in this order:

1. `Waiting for your approval to send SLACK-01.` — `C-C001 · 2/3`, with **Approve and send**
2. `Two identical things exist. I stopped rather than guess.` — `C-7001 · 2/3`
3. `Done. 3 of 3 systems confirmed.` — `C-6001 · 3/3`

The footer reads `3 job(s) · 1 waiting for you`.

> "This is that engine for somebody who is not an engineer. Three jobs: one finished, one
> stopped because it could not prove ownership and asked instead of guessing, and this one
> waiting for a person."

Click **Approve and send**.

> "It records who approved — an unnamed approval is refused."

The first card becomes `Done. 3 of 3 systems confirmed.` (`C-C001 · 3/3`), the footer becomes
`3 job(s) · 0 waiting for you`, and a line appears above the list: **Nothing is waiting for
you.** Because it is true, and a queue that empties should say so.

### 1:25 — back to the console. In the **Ask in words** panel, click **read it**

> "That has all been a request written in code, which is a fair thing to be suspicious of. So
> here is the entry point."

The panel answers `ACCEPTED`, `proposer: rule-based`, and prints four decisions:

```
conflict key    ACME::RENEWAL::KICKOFF
authority       approval → gmail · time → gmail · meeting → calendar · work → linear
effects         CALENDAR-01, LINEAR-01, SLACK-01
frozen digest   (whatever the page shows)
```

> "A sentence in, and a proposer offers the contract. Note what the proposal does *not*
> decide: which system has authority over which fact, the conflict key, the postconditions,
> who gets contacted. Those are the operator's — and the model cannot be talked into
> setting them."

Then click **a proposal that widens its own authority**. It answers `REJECTED`,
`proposer: rule+overreach`, with two reasons quoted verbatim on screen:

```
proposal sets 'authority': the authority mapping is the operator's, so a proposal may not set it
proposal sets 'recipients': who gets notified is an operator decision, never a proposer's
```

> "Same request, with a proposal that reroutes the work authority and widens the recipients.
> Refused on the field, by name, before anything was frozen. A model can propose. It cannot
> hand itself more power."

### 1:45 — scroll to the counters, right-hand column

```
INVARIANT COUNTERS
  3 intents · 2 committed · 0 refused · 0 reconciled
  0 duplicates prevented · 0 duplicates written · 0 false commits
  0 LLM-approved completions
```

> "Three jobs, two committed, zero false commits — computed by walking every commit and
> re-reading the ledger, not typed into the page."

Then point at `AUDIT CHAIN` below it, which ends with
`rows unedited: yes ✓ · whole-log chain linked: yes ✓`.

> "And the audit chain is hash-chained, so the record of what happened can be tampered with
> exactly never."

### 2:00 — stop

---

## If you have to cut something

Drop the first intake click at 1:25 and keep the adversarial one. The refusal is the point;
the acceptance is context.

## If the numbers don't match

The first click should end `COMMITTED` with `matching events 1`. If it ends `IDEMPOTENT`
with `matching events` above 1, `wrote this run` all zeros, and a Commit gate reason reading
*this intent already exists in state COMMITTED*, then the ledger already held this work when
you started. Nothing is broken — the engine is telling you the work was already done, which
is what it is for. Open `http://127.0.0.1:8000/fresh`, confirm the header reads
`ledger · fresh`, and click the button again.

## If you get asked

**"Is this just idempotency keys?"** No. Idempotency keys require the API to cooperate.
Gmail and Calendar never saw your intent id and do not participate in your transaction.
CAUSAL identifies an equivalent effect *after the fact* by its meaning, and when more than
one matches it refuses instead of guessing.

**"What stops the model from doing something else?"** It has no code path into the decision.
The engine, policy, ledger, registry and audit modules import no model client, and a test
reads their imports to enforce it. The model proposes at intake; deterministic code accepts
or refuses. The console reports `commits decided by a model: 0` as an aggregate over
persisted counters — a test consults the hook by hand to prove that number can leave zero.

**"Are these real apps?"** Yes, and each is verified live: Linear (issue created and read back through a *different* operation), Gmail (profile and messages read through the real API), Calendar (event created, read back by id, removed). `scripts/verify_live.py --write` reports 4 live, 0 unconfigured. The badge says `LOCAL` for the console's own run because that recording exercises the protocol against in-process services on purpose: it lets the fault injections run. The live adapters are the same interface, proven separately.

**"What if the API says it worked and it didn't?"** Then nothing commits. The write response
is never trusted: a different call reads the object back, the object must carry the intent's
tag, and the registered postcondition must hold. There is a whole button for this — **The API
succeeds and the action still fails** — where the write reports success, the read disagrees,
and the job blocks.

**"Why does the review page say nothing is waiting for me?"** Because nothing is. That page
only asks you about effects that leave the company; everything else the agent does on its own.
Run a sequence in the console and press **check again**.
