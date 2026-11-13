# Firstcall PR3: typed notes, follow-ups and outcomes (build plan, rev 2)

Source: `builds/v01/plan.md`, "PR3: typed notes, follow-ups and outcomes" (F30 to F35), with
the definitions it relies on: lead states, cadence and follow-ups, buyer profile, outcomes,
events, the no-model fallback, note scripts, the notes floors and the missed-floor rule.
Branch `notes-followups`, cut from main at 76b30e6. Rev 2 answers the advisor's 23 points;
the last section maps each one to where it is answered.

## Outcome

An owner, or a team lead of the lead's team, writes a note on a lead. When the note records a
call, text, email or meeting, they pick that action, and it logs a real contact. A model reads
each note into a tidied note, buyer profile (BANT) updates and suggested completed actions.
A suggestion logs nothing until someone taps Confirm. Time to first real contact is stored. The
"owned, no real contact" escalation fires until a contact is logged. Each team runs a nightly
follow-up job on its cadence, with no model call. Owners and team leads set outcomes. A buyer
writing again reopens the lead or alerts, as the transition table says. Team leads and admins
reopen by hand. Admins delete a lead. The note eval runs on seed-11 scripts as text and
reports against the notes floors.

## Out of scope

- Voice (PR4). The reader takes text only.
- The dashboard figures (PR5). PR3 stores what they need: each follow-up's due date and done
  time, and the outcome history.
- Editing or deleting a single note. Notes are append-only; delete lead removes them all.
- Model-written follow-up steps, and logging any action without a person's tap.
- An injection floor for notes. Notes are typed by signed-in staff, not buyers.
- A model pass for the note reader. It uses PR1b's pick, Claude Haiku 5.5.
- Contacts on pool or closed leads. Claim or reopen first.
- UI beyond plain working controls.

## Two PRs (confirmed with the orchestrator; Cristian to confirm)

- **PR3a, rules (no key):**
  - note scripts, gold and the frozen note scorer
  - typed notes with an action, contacts and time to first contact
  - contact-due escalation and person profile edits
  - cadence and the nightly job
  - outcomes, reopen and buyers writing again
  - delete lead, and pages
  - covers F30, F33, F34 and F35
- **PR3b, the note reader:**
  - the `read_note` task and note jobs
  - Confirm and Dismiss, and "Needs a model key"
  - the eval runner and the paid runs
  - covers F31 and F32

Each half leaves main working alone. A single PR would be about PR2's size (around 30 review
slices). The split keeps all model spend in one small PR, and lands the note gold and the
scorer on main before any reader code exists. PR3b branches from main after PR3a merges.

## Fixed now (ADR-007, committed with the gold in phase 1)

These values are fixed before any gold exists, so PR3b's schema cannot drift from the gold:
- **Authority:** `decision_maker`, `shares_decision`, `not_decision_maker`, `unknown`,
  plus `others` (free text).
- **Action kinds:** `call`, `text`, `email`, `meeting`. No direction: a contact counts in
  either direction.
- **Budget:** `text`, plus `min` and `max` as whole US dollars, either may be null. A single
  figure is min = max.
- **Timeline:** `text`, plus `month` as `YYYY-MM`, worked out against the note's date.
- **Need** and **notes:** free text.
- **Suggested action:** a kind, plus `quote`, the note's own words that show it happened.
- **Reader output:** `clean_note`, `budget`, `authority`, `need`, `timeline`, `notes`,
  `actions`. A field the note does not support is null.

`firstcall/core/note_terms.py` (vocabulary only, no reading code) holds these constants. A
test pins the gold, ADR-007 and, in PR3b, the answer schema to them.

## The note eval (rules frozen in phase 1, before any model call)

The scorer `firstcall/evaluation/note_score.py` and its tests are committed in phase 1 with
the gold. After phase 1, any change to the scorer needs a gold CHANGELOG entry and the
orchestrator's yes, and none is allowed once the seed-11 run has started.

**Scripts:** 150 per seed, for seeds 7 and 11:
- 60 with a completed action
- 75 hard non-actions: 15 each of future tense, negation, a plan, a hypothetical and a
  colleague's call
- 15 with neither

Gold fill rates are set so each BANT field is empty in 35% to 50% of scripts. "Always fill"
and "never fill" cannot pass.

**Held-out phrasing:**
- The generator has shared phrasings and seed-11-only phrasings: sentence templates, number
  styles and words for each action.
- At least 40% of seed-11 scripts use one or more seed-11-only phrasings, and a test checks
  that none of those appears in seed 7.
- The report splits seen and unseen phrasing. The floors apply to all seed-11 scripts, seen
  and unseen together (see the decisions for Cristian).

**Per field, value-aware:** a filled field counts as right only when its value is right. A
wrong value counts as one false fill and one miss.

| Field | Right when |
|---|---|
| Budget | min and max equal gold's, after number reading (below) |
| Authority | the enum equals gold's; `unknown` counts as empty |
| Need | it holds at least one of gold's need terms |
| Timeline | filled when gold is; the month equals gold's when gold has one |

- **BANT F1, floor ≥ 0.85:** computed per field, and each of the four must meet it.
- **Fill rate:** each field's predicted fill rate is shown beside gold's.
- **Authority exact and month exact:** two separate rates, each ≥ 0.85. Each is scored on the
  scripts whose gold has a value. False fills already count in F1.
- **Key-term recall, floor ≥ 0.80:**
  - Each gold term belongs to one field (budget, need, timeline or authority `others`) and
    counts only when found in that field.
  - The notes field and the tidied note never count.
  - A field that holds 80% or more of the script's words counts as no terms for that script.
  - The report shows how many fields were copied through.
- **Baseline rows:** the scorer also scores "always fill with a well-formed dummy", "never
  fill" and "copy the script into every field". A test asserts all three miss the floors.

**Unsupported facts (floor ≤ 1 of 150, as a count):** a script counts when an output text field
(budget, need or timeline text, authority `others`, notes or the tidied note) holds either of
these:
- **A number not in the script.** Numbers on both sides are read the same way:
  - digits with `,` or `.` separators, and decimals ("1.5")
  - a `$` sign
  - the suffixes `k`, `m`, `thousand`, `million` ("1.5 million" = 1,500,000; "$400k" = 400,000)
  - number words from zero to the millions ("forty thousand" = 40,000; "a hundred" = 100)

  A number counts as supported when the script has the same value. Ordinals, years and
  times-of-day are read as numbers too, so a year the model adds is caught. Budget
  min and max are checked the same way; the timeline month is not, since it is worked out
  from words like "next spring" by design and is scored by month exact.
- **A person's name not in the script.** A name is a capitalised word from the scorer's
  name list: Faker's en_US first and last names, minus months, weekdays and a fixed list of
  common English words such as "Will", "Rose" and "Mark".
  - This leaves out labels ("Budget:") and acronyms ("SUV", "HOA"), since neither is on the list.
  - It misses a made-up name that is not on the list. The report says so.

The scorer's tests cover each trap the advisor named: "2027-03" as a month, "forty thousand"
against 40000, "1.5 million", "$400k" against "$40k" (flagged), "Budget:" and "SUV" (not flagged).

**Actions:** precision and recall over (script, kind), with each non-action kind reported
apart. They are reported only, with no floor (v01 plan).

**Pass rule:**
- A floor is met when the point estimate meets it, as in PR1a and PR1b. The Wilson 95%
  interval is shown beside it; for F1, a fixed-seed bootstrap of 2,000 resamples in plain Python.
- An unreadable answer is scored as every field empty and no actions. It is never excluded,
  and it is counted.
- A run is complete only when every script has an answer or a logged unreadable result. An
  incomplete run writes no report. A resumed run answers recorded scripts from the recording,
  so it spends nothing on them.
- Every notes floor is a quality floor. A miss goes to `docs/failures/` under the
  missed-floor rule and does not block the merge.

**Held-out audit:**
- All recordings are kept, tuning included, in `evals/recordings/notes-claude-haiku-5-5.jsonl`.
- Seed 11 runs once, with the frozen prompt.
- The run commits its call log: count, tokens, cost and prompt hash.
- A test checks that seed 11 has exactly 150 recorded answers, all under the frozen prompt's
  hash, and none under any other prompt.
- The build log reports the ledger figure for the run and any failed attempts.

## Delete lead: a tombstone, not a row delete

Choice: keep the lead row and its events, scrub what identifies the buyer, delete the rest,
and append `lead.deleted` as the next event. Reasons, from the code:
- **Order holds.** `events.write` (`firstcall/events.py`) numbers each lead's events from
  `max(seq) + 1` under the lead's row lock. Keeping the earlier events means `lead.deleted`
  takes the next number with no gap, PR7 delivers in order, and nothing is removed from the
  outbox.
- **No foreign keys change.** `lead_events.lead_id` keeps its key to `leads`, so nothing can
  leave an orphan event.
- **Nothing in the events needs scrubbing.** Event payloads carry ids, team slugs and enums
  only (`process.py` writes `message_id`, `team` and `possible_duplicates` for
  `lead.created`). PR3 keeps that rule for its own events: `note.added` carries the note id,
  `lead.outcome` the outcome and reason code, never free text. A new test checks every
  event type's payload keys against an allowlist.

**What delete does, in one transaction, under the lead's row lock:**
- **Lead row:** name, phone, email and item set to null; `possible_duplicates`,
  `edited_fields` and `flags` emptied; `deleted_at` set (a new column). The row stays only as
  the events' anchor.
- **Messages:** each row stays, with `raw` emptied and `extracted`, `model_answer`,
  `source_time` and `category` cleared. `delivery_key`, `body_sha256` and
  `clean_body_sha256` stay, so a provider retry of a deleted message is still a
  `duplicate_delivery` and cannot bring the lead back. These are hashes and provider ids,
  not buyer text. A genuinely new message from the same person later makes a new lead.
- **Deleted outright:** identities, activities, notes, contacts, suggestions, profile,
  outcomes, alerts and their push sends, escalations, follow-ups, and the messages' jobs.
- **Rows not keyed by lead:**
  - An inbox email whose `links` include `/leads/<id>` is deleted, even when it is a digest
    that names other leads. Those leads stay on the board.
  - The id is removed from every other lead's `possible_duplicates`.
  - Model call rows keep their cost, with the note link cleared, for the cost page.
- **Email already sent** through SMTP cannot be recalled. The docs say so.
- **The event:** `lead.deleted`, with the next sequence number and a payload of
  `{"by": <user id>}`.
- **Afterwards:** a deleted lead answers 404 on every route, appears in no list, and refuses
  every transition. Its identities are gone, so no message can match it. A message job still
  in flight when the delete runs re-runs matching and makes a new lead.

**The delete test** builds a lead with a row in every table, deletes it, then walks
`Base.metadata.tables`. Each table must be listed in the test as one of these:
- deleted for this lead
- kept and scrubbed, with its scrubbed columns named
- not lead data

A table that is in none of the three fails the test. So when PR3b adds suggestion and note
job tables, it has to classify them.

## Phases

Builders commit at each phase end, build folder included. Test first. `./verify.sh` passes
at every phase end.

### PR3a

**Phase 1: note gold, ADR-007 and the frozen scorer (before any reader code).**
- `firstcall corpus generate` writes `notes.jsonl` and `notes_labels.jsonl` for seeds 7 and
  11. It draws them from a separate random stream, so every existing gold file stays byte for
  byte the same.
- Also: the manifest entries, a CHANGELOG entry, ADR-007 with its prediction,
  `core/note_terms.py`, and `evaluation/note_score.py` with its tests.

| Check | Maps to |
|---|---|
| Counts per seed: 150, 60 actions, 15 of each non-action kind, fill rates 35% to 50% empty | F32 |
| At least 40% of seed-11 scripts use seed-11-only phrasings, and none of those appears in seed 7 | F32 |
| Existing gold hashes unchanged; note files in the manifest | F32 |
| Every gold term, number and name appears in its script; gold values use only the ADR-007 vocabulary | F32 |
| Scorer tests: each trap above, per-field value scoring, the copy-through rule, all three baselines missing the floors | F32 |
| New test: note gold, ADR-007 and the scorer committed before any reader code, checked by `git log` and safe under squash merges like the existing check | F32 |

**Phase 2: notes, contacts, outcomes and delete (backend).**

Migration: `notes`, `contacts`, `lead_profiles` and `outcomes`, plus lead columns
`first_contact_at`, the first-contact minutes (wall-clock and business), `uncontacted_since`
and `deleted_at`.

**Who logs a contact:** the owner, a team lead of the lead's team, or an admin, on an owned or
nurture lead. This follows the role table ("everything a salesperson can, on every lead in
their teams"). A typed note with an action logs the contact, `note.added` and
`contact.logged`, and marks the open follow-up done.

**First contact:** `first_contact_at` is the earliest contact time on the lead.
- A contact earlier than the stored one replaces it, with its minutes worked out again. This
  covers a backdated confirm in PR3b.
- A contact can never be dated before the lead's arrival.

**One contact per note and kind:** `contacts` is unique on (note, kind). A typed action and a
suggestion of the same kind on one note log one contact.

**The contact-due clock:** it runs from `uncontacted_since`, the start of an uncontacted stretch.
- It is set when a lead becomes owned and it is null, and on every reopen (buyer or manual).
- Any contact clears it.
- Release, reclaim, reassign, nurture and resume never reset it, so release-and-reclaim
  cannot dodge it.
- It counts business minutes. The owner is told at 240 and the team leads at 300, only while
  the lead is owned (not nurture).
- Each level fires once per stretch, keyed by `escalations.stay = uncontacted_since`.

**Outcomes:** they go through `core/states.py`, which already holds every row.
- Lost needs a reason code from a fixed list: price, location, timing, bought elsewhere, not
  ready, unreachable, or other with text. A competitor is optional.
- `outcomes` is insert-only.

**A buyer writing again**, in the attach path of `pipeline/process.py`:
- A lost or not-real lead reopens only when the message's category is `lead` and it is not an
  auto-reply (an `Auto-Submitted` header other than `no`). Any other message attaches as
  activity and alerts, with no reopen.
- The reopen goes to the owner if they are still active and in the team, otherwise to the pool.
- When a won lead's owner is inactive or has left the team, the alert goes to the team's leads.
- The reopen event's cause is `message:<id>:reopen`, apart from the activity's
  `message:<id>`, because `lead_events` is unique on (lead, cause).
- A `duplicate_content` message returns before any of this.

**Person edits:** they go to the profile and are recorded as person-set fields.

**Delete lead:** as above.

**The monitor:** gets no note, profile, contact or outcome route. The lead detail payload a
monitor receives carries no notes, contacts, profile or follow-up.

| Check | Maps to |
|---|---|
| A note with an action logs one contact and sets first contact (business and wall-clock). A note without one is no touch. An earlier contact replaces the first-contact time. A typed action plus a same-kind second log on one note makes one contact | F30 |
| Contact-due levels: they fire at 240 and 300 and not before, once; a contact stops them; release-and-reclaim and reassign don't reset them; nurture pauses them; a reopen starts a new stretch; a double tick sends nothing twice | F30 |
| Lost needs a reason; nurture stays open; won-then-writes alerts the owner, or the team leads when the owner is inactive or gone; lost and not-real reopen to the active owner or the pool; an auto-reply or non-lead message does not reopen; `duplicate_content` does nothing; a reopen and its activity write two events; manual reopen works; `outcomes` refuses UPDATE and DELETE | F34 |
| The delete test over every table, as above; a redelivered message is `duplicate_delivery`; a deleted lead answers 404 and is in no list | F35 |
| Every event type's payload keys match the allowlist | F35 |
| A new access matrix test over the note, contact, profile, outcome, reopen and delete routes, for every role, on its own team and another team. Every refused pair gets 404 with no data. The monitor's detail payload has no notes or profile | F35 |

**Phase 3: cadence and the nightly job.**

`followups` and `followup_runs` tables, and `core/cadence.py`, pure, with an injected clock.

**The steps:**
- Steps fall on the arrival's local date plus 0, 1, 3, 7, 14 and 30 days. After that the
  team's steady rhythm takes over: daily 1 day, weekly 7, monthly 30, counted from the later
  of day 30 and the last touch.
- Nurture runs every 30 days from the later of entering nurture and the last touch.
- A step that lands on a day the team is closed moves to its next open day.

**The due date:**
- At 05:00 team time, the job opens a follow-up for the current step on its due date
  (`due_on`), unless a touch has happened on or after the step's date.
- A follow-up whose due date has passed stays open and shows as overdue.
- **On time** means done on or before `due_on`, which gives PR5 a measurable figure.
- A lead has at most one open follow-up; a later run moves it to the newest due step.
- The next step line gives the days since the last touch and the first missing BANT field.

**A follow-up's life:**
- It belongs to the current owner and moves with a reassign.
- Any touch closes it with `followup.done`.
- When the lead leaves owned or nurture, it closes with `followup.cancelled` (a new event
  type, see the decisions for Cristian), so a CRM never keeps an open task.

**One run per team and local date:**
- The run row and every follow-up change commit in one transaction. A crash leaves nothing
  half-done, and the next tick redoes the run.
- Each lead runs in its own savepoint. A lead that raises is logged, counted in the run row's
  `errors`, and skipped, so one bad lead cannot block its team.
- A missed night is caught up by the next tick, as one run for today's date.

| Check | Maps to |
|---|---|
| Each rhythm, the default cadence, nurture, and a step on a closed day | F33 |
| DST spring forward and fall back; a team (Pacific/Auckland) whose local date at 05:00 differs from the UTC date | F33 |
| A double run, a missed night and one open follow-up per lead; a lead that raises is skipped and counted; a failpoint `during_followup_run` kills the job mid-run, the exit and log line are asserted, and the next tick gives one complete run | F33 |
| On time and overdue are told apart by `due_on` | F33 |
| A follow-up is cancelled when its lead leaves owned or nurture | F33 |
| The nightly job makes zero gateway calls | F33 |

**Phase 4: pages.**
- **Lead detail:** notes with an add-note form and an action select; the profile with an edit
  form; time to first contact; the open follow-up; outcome and reopen controls; delete for
  admins.
- **My leads:** due and overdue follow-ups first.
- **Playwright on compose:** an owner adds a note with "Called" and sees the contact and the
  time to first contact; sets lost with a reason; a team lead reopens; an admin deletes; a
  `<script>` note renders as text. Covers F30, F34 and F35.

### PR3b

**Phase 5: the note reader (no key).**
- **The task:** `read_note`, with a fixed system prompt. Only the note text goes in the user
  message; the note date and the team's time zone go in as context. The schema comes from
  `core/note_terms.py`.
- **Gateway:** `ModelRequest` and `model_calls` gain a nullable `note_id`. Record mode's
  synthetic check also accepts note scripts and `evals/gold/unit-notes.jsonl`.
- **Note jobs:** `jobs.message_id` becomes nullable and `note_id` is added, with a check that
  exactly one is set, unique on (note, kind).
  - `claim` drops its inner join to `messages` and orders by the job's own creation time.
  - `run` dispatches by kind.
  - `_fail` today only updates a message (`pipeline/jobs.py:38`). For a note job it sets the
    note's `read_state` to `failed`, reason `retries_exhausted`. The note text and any typed
    action stay, and the page says "Couldn't read this note".
  - The reaper re-queues or fails note jobs exactly as it does message jobs.
- **Suggestions:**
  - Confirm and Dismiss need the same rights as logging a contact (owner, team lead of the
    team, admin). Anyone else gets 404.
  - Confirm logs one contact, dated at the note's creation and made by the note's author.
  - A suggestion whose kind the note already logged is closed as "already logged".
  - After a reassign, the old owner's open suggestions stay on the note for the new owner or
    a team lead to confirm or dismiss.
  - A suggestion on a lead that is no longer owned or nurture cannot be confirmed until the
    lead is.
- **Profile merge:** a newer note's model value replaces an older model value. An empty model
  value never clears a field. A person-set field is never touched. The profile's notes field
  gains one line per note.
- **No model:** with no key, the kill switch on, the budget hit or the model down, the note
  stays and its profile and suggestions say "Needs a model key". The reread pass reads it once
  the model is back.
- **The eval runner:** it wires the frozen scorer to the gateway, in replay mode and on fakes.

| Check | Maps to |
|---|---|
| A suggestion logs nothing until confirmed; Confirm logs one contact, and a second Confirm none; Dismiss logs none; a same-kind typed action means no second contact; an older note confirmed after a later contact moves first contact back | F31 |
| Confirm and Dismiss are added to the new access matrix test; the delete test classifies the new tables | F31, F35 |
| A suggestion whose quote is not in the note is dropped | F31 |
| A person-edited profile field survives a model answer | F31 |
| With no key: the note is saved, says "Needs a model key", and nothing returns 500 | F31 |
| A note job reaped after its lease runs out runs again; one that exhausts its retries ends `failed` with the reason, and its note says so | F31 |
| The eval runner writes every metric, interval, split and baseline from fake answers | F32 |

**Phase 6: pages for suggestions.**
- Confirm and Dismiss on the note, and "Needs a model key" or "Couldn't read this note" where
  either applies.
- Playwright on compose in replay mode, against a recorded fixture note.

**Phase 7: the paid runs (only after your go).**
1. A smoke test of 5 calls on seed-7 scripts.
2. Prompt tuning on seed 7, at most 6 passes.
3. The prompt is frozen and its hash written to the build log.
4. Seed 11 runs once.
5. The five fixed F31 notes (future, negation, plan, hypothetical, a colleague's call) are
   recorded once, with the frozen prompt, and their replay test is added. If any of them gets
   a suggestion, that is an F31 failure and I call back. They are never re-recorded to turn
   green.

| Check | Maps to |
|---|---|
| The seed-11 report, with intervals, seen and unseen phrasing and baselines, is committed, and replay reproduces it in verify | F32 |
| Exactly 150 seed-11 answers, all under the frozen prompt hash; the ledger and the call log are in the build log | F32 |
| Every miss is in `docs/failures/` with its numbers and likely cause; ADR-007 is closed with the result | F32 |
| The five F31 notes pass in replay | F31 |

## Spend for the note eval

- **Model:** Claude Haiku 5.5 (`claude-haiku-5-5`) on the Firstcall key: a $10 limit, $0.35
  spent so far.
- **Per call:** about 1,200 prompt tokens and 250 answer tokens, so 1,200 × $0.10/M +
  250 × $0.50/M ≈ $0.00025. PR1b measured $0.00022 a call on messages.
- **Runs:** 5 smoke calls, up to 6 passes of 150 on seed 7, 150 on seed 11, and 5 F31 notes.
  That is 1,060 calls, **about $0.27**.
- **Cap: $0.50 for PR3.** Each run passes `--budget` set to what is left, and the daily ledger
  cap stays $1.00. A seventh tuning pass, or anything that would go past $0.50, means I stop
  and call back. No live call happens before your go.

## Decisions for Cristian

1. **`followup.cancelled`, a new event type.** The v01 event list has no way to close a
   follow-up without a touch. Without it, a CRM consuming PR7's events keeps the task open
   forever. *Recommend:* add it, and amend the list in `docs/definitions.md`.
2. **Delete keeps a scrubbed tombstone.** The lead row stays with its contacts emptied; the
   event history (ids and enums only) stays; message rows keep their delivery key and body
   hashes, so a retry cannot recreate the lead. Everything readable about the buyer is gone.
   *Recommend:* yes. The other option, deleting every row, breaks PR7's ordered outbox and
   lets a provider retry recreate the lead.
3. **Notes floors cover unseen phrasing too.** Messages put floors on seen layouts only. For
   notes, *recommend* floors on all seed-11 scripts: notes are free text from day one, so
   unseen phrasing is the normal case, not an edge.
4. **Only a real lead message reopens a lost or not-real lead.** An auto-reply, or a message
   classed vendor, support or spam, attaches and alerts without reopening. *Recommend:* yes.

## Other choices (my call, stated so they can be overruled)

- **When a contact happened:** when the note is saved for a typed action; the note's creation
  time for a confirmed suggestion.
- **Tidied note:** stored beside the typed text, which stays the record, and shown under it
  as "Tidied".
- **Access matrix for F35:** a new test reusing the F20 harness, so the existing matrix test
  is not edited.
- **Steady rhythm lengths:** daily 1, weekly 7, monthly 30 days.

## The advisor's points, answered

| # | Point | Answer |
|---|---|---|
| 1 | Unsupported-fact rule misfires; scorer not frozen | Number reading (words, suffixes, decimals), name list instead of capitals, month field excluded; scorer and trap tests frozen in phase 1 |
| 2 | F1 passes a bad reader | Value-aware F1 per field, each ≥ 0.85; fill rates shown; empty rate 35–50%; baselines must fail; key terms per field, copy-through excluded |
| 3 | Seed 11 not held out | Seed-11-only phrasings in ≥ 40% of its scripts, never in seed 7, reported apart |
| 4 | Pass rule undefined | Point estimate, interval shown; authority and month separate; unreadable scored empty; incomplete run writes no report |
| 5 | "Once" unauditable | All recordings kept; test: exactly 150 seed-11 answers under the frozen hash only; ledger in the log |
| 6 | Thin evidence | 150 scripts, 15 per non-action kind; F31 notes recorded once with the frozen prompt, never re-recorded |
| 7 | Gold before schema | ADR-007 and `core/note_terms.py` fix authority, action kinds and formats in phase 1 |
| 8 | Confirm/Dismiss access; unlisted tables | Both in the access matrix; the delete test fails on any unclassified table |
| 9 | Lease and reaper | `claim` join, dispatch and `_fail` changed for note jobs; reap and exhaust tested |
| 10 | Event sequence on delete | Tombstone: events kept (ids and enums only, tested), `lead.deleted` takes the next seq, FK unchanged |
| 11 | Redelivery resurrects | Message rows keep delivery key and hashes; retry is `duplicate_delivery` (test) |
| 12 | Rows not keyed by lead | Inbox emails by link path; other leads' `possible_duplicates`; model calls keep cost |
| 13 | Clock evadable | `uncontacted_since` stretch: not reset by release, reclaim or reassign; nurture pauses; reopen restarts |
| 14 | Backdated first contact | First contact is the earliest contact, recomputed |
| 15 | Double logging | Contacts unique on (note, kind) |
| 16 | Owner-only contact | Owner, team lead of the team, admin; stranded suggestions confirmable by the new owner or team lead |
| 17 | Reopen event key | Reopen cause `message:<id>:reopen`; `duplicate_content` and auto-reply tested |
| 18 | Departed owners on closed leads | Won alert falls to team leads; reopen to pool; both tested |
| 19 | Monitor payload | Test that the monitor's detail payload has no notes, contacts, profile or follow-up |
| 20 | Due date undefined | Follow-up opens on the step's due date; on time = done by `due_on`; closed days shift |
| 21 | One lead blocks a run | Savepoint per lead, one transaction per run, failpoint test |
| 22 | Silent close | `followup.cancelled` (decision 1) |
| 23 | DST coverage | Both directions and Pacific/Auckland |
