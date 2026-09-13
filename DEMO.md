# CAUSAL — demo script

Two minutes. Six clicks. Everything you press runs the real engine — the same code the
237-test suite and the 100-run campaign drive. No mock, no slide, no pre-filled panel.

Every number quoted below was read off the running console in exactly this click order,
on a freshly reset ledger. The counts are the real objects in the world, so if you press
a button twice they will change — that is the system telling the truth, not a bug.

**Before you record**

```bash
cd ~/maah/causal
uv run uvicorn causal.api:app --port 8000
```

Open `http://127.0.0.1:8000` in one tab and `http://127.0.0.1:8000/review` in a second.
Press **reset ledger** once in the left rail. Do not press it again after that.

Then, before you hit record, prove the script still matches the console:

```bash
uv run python scripts/demo_preflight.py
```

It walks this exact click order and checks every number quoted below, printing the real
value beside any claim that has drifted. Thirty-one claims, and it exits non-zero if one
moves.

**After you record:** paste the video link into the README's `## ▶ Demo` section, which
currently holds the literal placeholder `PASTE_VIDEO_LINK_HERE`. Then run the release gate:

```bash
uv run python scripts/verify_all.py
```

That gate fails on `demo video link is present` until the placeholder is replaced, so the
repository cannot be submitted with an empty slot in it. If you upload the video to a
GitHub issue or pull request, the resulting `https://github.com/user-attachments/assets/...`
URL renders as an inline player in the README; a YouTube (unlisted) or Loom link also works
and reads better on the submission form.

---

### 0:00 — the console, nothing clicked

> "This is an agent that touches Gmail, Calendar and Linear to finish one real job. Every
> problem it exists for starts the same way: the write succeeds and the answer gets lost.
> So there is one rule. Nothing is marked done because an API returned 200."

Point along the sentence across the top.

> "EFFECTED, VERIFIED and COMMITTED are three different things. Done means the effect was
> read back from the system that owns it, and it belongs to exactly one intent."

### 0:10 — click **Worker A dies holding the job — worker B takes over, no duplicate**

> "Worker A writes the calendar event, then dies before it records anything. Worker B
> picks the job up. It does not retry. It asks the world what already happened."

Point at **wrote this run** in the first panel — `"calendar":0`.

> "Worker B wrote zero calendar events. It found A's event by its meaning — this renewal,
> that date, those two people — and adopted it. Then it finished only what was missing."

Point at **events_matching: 1** and at **truth**, which shows exactly one calendar event
in the world after two workers.

> "And when it commits, it proves the chain: both workers computed the same digest over
> the same intent."

Point at the verdict lines and the two audit facts at the bottom of the panel:
**rows unedited: yes**, **whole-log chain linked: yes**.

### 0:35 — click **An equivalent effect already exists — refuses to commit**

> "Now the hard case. Identical events exist in the calendar. One of them is ours and the
> system cannot prove which. It refuses."

Point at the **candidates** count and the **AMBIGUOUS** chip on CALENDAR-01.

> "It had what looks like successful evidence sitting right there, and it still will not
> commit. That refusal is the product. Any system can report success; knowing when success
> cannot be proven is what makes the success worth anything."

### 0:55 — click **Outbound waits for a person; internal effects do not**

> "The same intent, three effects. The calendar and the task are already confirmed. The
> one that reaches the outside world is held."

Point at the three effect rows: two **VERIFIED**, one **AWAITING_APPROVAL**.

> "Two of three were done before anyone was asked. Only the notice that leaves the company
> waited — and the job is not committed while it waits."

### 1:05 — switch tabs to `/review` — the same engine in words

> "This is that engine for somebody who is not an engineer."

Point at the three jobs. Then click **Approve and send**.

> "Three jobs. Two done, one stopped because it could not prove ownership and asked
> instead of guessing. And this one was waiting for a person. It records who approved —
> an unnamed approval is refused."

The row turns into **Done. 3 of 3 systems confirmed.**

### 1:25 — back to the console. In the **Ask in words** panel, click **read it**

> "That has all been a request written in code, which is a fair thing to be suspicious of.
> So here is the entry point."

Point at the accepted contract: the conflict key, the authority mapping, the digest.

> "A sentence in, and a proposer offers the contract. Note what the proposal does *not*
> decide: which system has authority over which fact, the conflict key, the postconditions,
> who gets contacted. Those are the operator's — and the model cannot be talked into
> setting them."

Then click **a proposal that widens its own authority**.

> "Same request, with a proposal that reroutes the work authority and widens the
> recipients. Refused on the field, by name, before anything was frozen. A model can
> propose. It cannot hand itself more power."

### 1:45 — scroll to the counters, bottom right

> "Three jobs, two committed, zero false commits — computed by walking every commit and
> re-reading the ledger, not typed into the page. And the audit chain is hash-chained, so
> the record of what happened can be tampered with exactly never."

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
