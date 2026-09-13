# CAUSAL — demo script

Two minutes. Six clicks. Everything you press runs the real engine — the same code the
237-test suite and the 100-run campaign drive. No mock, no slide, no pre-filled panel.

**Every label quoted below was read off the running console in exactly this click order.**
Where it says *point at*, that string is on screen.

**Before you record**

```bash
cd causal
uv run uvicorn causal.api:app --port 8000
```

Open `http://127.0.0.1:8000` in one tab and `http://127.0.0.1:8000/review` in a second.
Press **reset ledger** once in the left rail. Do not press it again after that — the counts
are the real objects in the world, so a second press of the same button reports different
numbers, honestly.

Then prove the script still matches the console:

```bash
uv run python scripts/demo_preflight.py
```

It walks this click order against the running console, checks 53 numbers, and checks that
every label this file points at still exists in the page.

**After you record:** paste the video link into the README's `## ▶ Demo` section, which
currently holds the literal placeholder `PASTE_VIDEO_LINK_HERE`. Then run the release gate:

```bash
uv run python scripts/verify_all.py
```

That gate fails on `demo video link is present` until the placeholder is replaced, so the
repository cannot be submitted with an empty slot in it. A GitHub issue/PR upload
(`https://github.com/user-attachments/assets/...`) renders as an inline player; a YouTube
(unlisted) or Loom link also works and reads better on the submission form.

---

### 0:00 — the console, nothing clicked

> "This is an agent that touches Gmail, Calendar and Linear to finish one real job. Every
> problem it exists for starts the same way: the write succeeds and the answer gets lost.
> So there is one rule. Nothing is marked done because an API returned 200."

Point along the sentence across the top: `EFFECTED ≠ VERIFIED ≠ COMMITTED`.

> "Done means the effect was read back from the system that owns it, and it belongs to
> exactly one intent."

### 0:10 — click **Worker A dies holding the job — worker B takes over, no duplicate**

Point at the top panel, in this order:

| on screen | what it says here | say |
|---|---|---|
| `wrote this run` | `{"calendar":0,"linear":1,"slack":1}` | "Worker B wrote zero calendar events." |
| `read back from apps` | `{"calendar":1,...}` | "It found A's event by its meaning — this renewal, that date, those two people — and adopted it." |
| `matching events` | `1 — the world holds this many, whatever the writers claim` | "Two workers, one real event." |
| `worker A's effect` | `ext-0001 — found by its meaning, not by an id we kept` | "It never had to store A's id." |
| `while the lease was live` | `IDEMPOTENT · 0 writes · ...` | "The second worker was told to wait, not to write." |

Then the **EFFECTS** table (three rows, all `VERIFIED`), then the **COMMIT GATE** panel:

> "And it proves the chain: `rows unedited: yes ✓ · whole-log chain linked: yes ✓` — the
> audit log is hash-chained, so the record cannot be edited without it showing."

### 0:35 — click **An equivalent effect already exists — refuses to commit**

Point at the **EFFECTS** table: `CALENDAR-01` carries the `AMBIGUOUS` chip. Then at the
`matching candidates` row in the top panel.

> "Now the hard case. Identical events exist in the calendar. One of them is ours and the
> system cannot prove which. It refuses — the commit gate says
> `required effect CALENDAR-01 is AMBIGUOUS, not VERIFIED`."

> "It had what looks like successful evidence sitting right there, and it still will not
> commit. That refusal is the product. Any system can report success; knowing when success
> cannot be proven is what makes the success worth anything."

### 0:55 — click **Outbound waits for a person; internal effects do not**

Point at the three rows of the **EFFECTS** table:

```
CALENDAR-01   calendar   VERIFIED            evt-0002   1
LINEAR-01     linear     VERIFIED            lin-0003   1
SLACK-01      slack      AWAITING_APPROVAL   —          0
```

> "The same intent, three effects. The calendar and the task are already confirmed. The one
> that reaches the outside world is held, and the commit gate says
> `required effect SLACK-01 is AWAITING_APPROVAL, not VERIFIED`."

> "Two of three were done before anyone was asked. Only the notice that leaves the company
> waited — and the job is not committed while it waits."

### 1:05 — switch tabs to `/review` — the same engine in words

Point at the heading and the three job cards, in the order they appear:

1. `Waiting for your approval to send SLACK-01.` — `C-C001 · 2/3`, with the button
   **Approve and send**
2. `Two identical things exist. I stopped rather than guess.` — `C-7001 · 2/3`
3. `Done. 3 of 3 systems confirmed.` — `C-6001 · 3/3`

> "This is that engine for somebody who is not an engineer. Three jobs: one finished, one
> stopped because it could not prove ownership and asked instead of guessing, and this one
> waiting for a person."

Click **Approve and send**.

> "It records who approved — an unnamed approval is refused."

The card becomes `Done. 3 of 3 systems confirmed.` and the footer reads
`3 job(s) · 0 waiting for you`.

### 1:25 — back to the console. In the **Ask in words** panel, click **read it**

> "That has all been a request written in code, which is a fair thing to be suspicious of.
> So here is the entry point."

Point at the four lines the panel produces: `conflict key` (`ACME::RENEWAL::KICKOFF`),
`authority` (`approval → gmail · time → gmail · meeting → calendar · work → linear`),
`effects`, and `frozen digest`.

> "A sentence in, and a proposer offers the contract. Note what the proposal does *not*
> decide: which system has authority over which fact, the conflict key, the postconditions,
> who gets contacted. Those are the operator's — and the model cannot be talked into
> setting them."

Then click **a proposal that widens its own authority**. It returns `REJECTED` with two
reasons, quoted verbatim on screen:

```
proposal sets 'authority': the authority mapping is the operator's, so a proposal may not set it
proposal sets 'recipients': who gets notified is an operator decision, never a proposer's
```

> "Same request, with a proposal that reroutes the work authority and widens the recipients.
> Refused on the field, by name, before anything was frozen. A model can propose. It cannot
> hand itself more power."

### 1:45 — scroll to the counters, in the right-hand column

> "Three jobs, two committed, zero false commits — computed by walking every commit and
> re-reading the ledger, not typed into the page."

Point at `INVARIANT COUNTERS`, then at `AUDIT CHAIN` below it, which ends with
`rows unedited: yes ✓  ·  whole-log chain linked: yes ✓`.

> "And the audit chain is hash-chained, so the record of what happened can be tampered with
> exactly never."

### 2:00 — stop

---

**If you have to cut something:** drop the first intake click at 1:25 and keep the
adversarial one. The refusal is the point; the acceptance is context.

## If you get asked

**"Is this just idempotency keys?"** No. Idempotency keys require the API to cooperate.
Gmail and Calendar never saw your intent id and do not participate in your transaction.
CAUSAL identifies an equivalent effect *after the fact* by its meaning, and when more than
one matches it refuses instead of guessing.

**"What stops the model from doing something else?"** It has no code path into the
decision. The engine, policy, ledger, registry and audit modules import no model client,
and a test reads their imports to enforce it. The model proposes at intake; deterministic
code accepts or refuses. The console reports `commits decided by a model: 0` as an
aggregate over persisted counters — a test consults the hook by hand to prove that number
can leave zero.

**"Are these real apps?"** The badge says `LOCAL`, and it says that on purpose. Linear is
live — verified by creating an issue and reading it back through a *different* operation.
Gmail and Calendar are wired and verified up to Google's consent screen: `verify_live.py`
proves the OAuth client is valid, and the one remaining step is a browser click no script
can give.

**"What if the API says it worked and it didn't?"** Then nothing commits. The write
response is never trusted: a different call reads the object back, the object must carry
the intent's tag, and the registered postcondition must hold. There is a whole button for
this — **The API succeeds and the action still fails** — where the write reports success,
the read disagrees, and the job blocks.
