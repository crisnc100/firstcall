# Firstcall PR3: typed notes, follow-ups and outcomes (build plan)

Source: `builds/v01/plan.md`, "PR3: typed notes, follow-ups and outcomes" (F30 to F35), with
the definitions it relies on: lead states, cadence and follow-ups, buyer profile, outcomes,
events, the no-model fallback, note scripts, the notes floors and the missed-floor rule.
Branch `notes-followups`, cut from main at 76b30e6.

## Outcome

An owner writes a note on a lead and, when it records a call, text, email or meeting, picks
that action, which logs a real contact. A model reads each note into a tidied note, buyer
profile (BANT) updates and suggested completed actions, which log nothing until the owner
taps Confirm. Time to first real contact is stored, and the "owned, no real contact"
escalation fires until a contact is logged. Every team runs a nightly follow-up job on its
cadence, with no model call. Owners and team leads set outcomes; buyers writing again reopen
or alert as the transition table says; team leads and admins reopen by hand; admins delete a
lead. The note eval runs on seed-11 scripts as text and reports against the notes floors.

## Out of scope

- Voice (PR4). The reader takes text only; PR4 puts transcription in front of it.
- The dashboard, "follow-ups done on time" and conversion figures (PR5). PR3 stores what
  they need (due date and done time on each follow-up, the outcome history).
- Editing or deleting a single note. Notes are append-only; delete lead removes them all.
- Model-written follow-up steps, and logging any action without a person's tap (v01 out of scope).
- An injection floor for notes. Notes are typed by signed-in staff, not by buyers; PR4
  revisits this for voice if the advisor asks.
- A model pass for the note reader. It uses the model PR1b picked (Claude Haiku 5.5).
- Any UI beyond plain working controls. The "less is more" redesign comes later.

## One PR or two: two (recommended; the v01 plan says one, so this needs your yes)

- **PR3a, rules (no key):** note scripts and gold, typed notes with an action, real
  contacts, time to first real contact, contact-due escalation, profile with person edits,
  cadence and the nightly job, outcomes, reopen and buyers writing again, delete lead, and
  the pages for all of it. F30, F33, F34, F35.
- **PR3b, the note reader:** the reader task in the gateway, suggestions with Confirm and
  Dismiss, the profile merge, "Needs a model key", the note eval and its paid runs. F31, F32.

Why: each half leaves main working alone (PR3a is a full typed-note product with no model;
PR3b adds the reader on top). PR2 needed about 30 review slices as one PR; this is the same
size. The split also keeps all model spend in one small PR, and lands the note gold on main
before any reader code exists, which the gold-before-code check wants. PR3b branches from
main after PR3a merges. If you want one PR, the phases below run unchanged in one branch.

## Phases

Builders commit at each phase end, build folder included. Test first. `./verify.sh` passes
at every phase end.

### PR3a

**Phase 1: note scripts and gold (before any reader code).**
- `firstcall corpus generate` writes `notes.jsonl` (the script) and `notes_labels.jsonl`
  (gold) for seeds 7 and 11, from a separate random stream, so every existing gold file
  stays byte for byte the same.
- Each seed: 100 scripts. 45 with a completed action; 45 hard non-actions, 9 each of future
  tense, negation, a plan, a hypothetical and a colleague's call; 10 with neither. Every
  script has gold BANT (each field present or empty; authority enum; timeline month as
  `YYYY-MM` against the script's note date), key terms, and completed actions by kind.
- Manifest entries, a CHANGELOG entry, and ADR-007 (the note reader) with its prediction.

| Check | Maps to | Pass |
|---|---|---|
| Counts per seed meet ≥ 80, ≥ 40 actions, ≥ 40 hard non-actions with all five kinds | F32 | test |
| Existing gold and manifest hashes unchanged; note files in the manifest | F32 | test |
| Every gold key term, number and name appears in its script | F32 | test |
| A new test: note gold and ADR-007 committed before any reader code (`git log`, squash-safe as the existing check) | F32 | test |

**Phase 2: notes, contacts, outcomes, delete (backend).**
- Migration: `notes`, `contacts`, `lead_profiles`, `outcomes`, `followups`,
  `followup_runs`; lead columns for first contact; `lead_events.lead_id` loses its foreign
  key so `lead.deleted` outlives the lead (see Decisions 9).
- Typed note with an optional action (call, text, email, meeting): the action logs a
  contact, writes `note.added` and `contact.logged`, sets first contact once, and marks the
  open follow-up done (`followup.done`).
- Contact-due escalation: two levels, owner at 240 and team leads at 300 business minutes
  from `owned_since`, once per ownership, stopped by any contact in that ownership.
- Outcomes and reopen through `core/states.py`, which already holds every row. The intake
  attach path adds the buyer-writes-again rows for nurture, won, lost and not real.
- Profile edits by a person, recorded as person-set fields.
- Delete lead (admin).

| Check | Maps to | Pass |
|---|---|---|
| A note with an action logs one contact and sets time to first contact (business and wall-clock); a note without one is no touch | F30 | test |
| Contact-due levels fire at 240 and 300 business minutes, not before, once; a contact stops them; a double tick sends nothing twice | F30 | fake clock |
| Lost needs a reason; nurture stays open; won-then-writes adds activity and alerts the owner or team lead; lost and not-real reopen to the active owner or the pool; manual reopen; outcome rows never update or delete | F34 | tests |
| Delete lead removes the lead, messages, identities, activities, notes, contacts, profile, outcomes, alerts, push sends, inbox mail about it, escalations, follow-ups and jobs; model calls keep their cost with the link cleared; one `lead.deleted` event remains | F35 | test reads every table |
| New access matrix test over note, profile, outcome, reopen and delete routes, every role, own team and other team, 404 with no data for every refused pair | F35 | test |

**Phase 3: cadence and the nightly job.**
- `core/cadence.py`: the default new-lead steps (days 0, 1, 3, 7, 14, 30), then the team's
  steady rhythm; nurture on monthly. Pure, with an injected clock.
- The nightly job from `firstcall tick`: per team at 05:00 team time, one `followup_runs`
  row per (team, local date), at most one open follow-up per lead, rules-based next step.

| Check | Maps to | Pass |
|---|---|---|
| Each rhythm and the default cadence give the right due dates | F33 | fake clock |
| Per-team time zones and a daylight-saving change | F33 | fake clock |
| A double run, a missed night caught up by the next tick, one open follow-up per lead | F33 | fake clock, Postgres |
| The nightly job makes zero gateway calls | F33 | gateway count |

**Phase 4: pages.**
- Lead detail: notes with an add-note form and action select, the profile with an edit
  form, time to first contact, the open follow-up, outcome and reopen controls, delete for
  admins. My leads: due follow-ups first, with their next step. Plain controls.
- Playwright on compose: an owner adds a note with "Called", sees the contact and time to
  first contact; sets lost with a reason; a team lead reopens; an admin deletes.

| Check | Maps to | Pass |
|---|---|---|
| The Playwright flow above passes | F30, F34, F35 | Playwright |
| A `<script>` note renders as text | F35 | Playwright |

### PR3b

**Phase 5: the note reader (no key).**
- A `read_note` task: fixed system prompt, note text only in the user message, the note
  date and the team's time zone as context. Schema: tidied note; budget (text, optional
  range); authority (enum, others); need; timeline (text, optional month); notes; actions,
  each a kind plus the quoted words that show it happened.
- The gateway logs a note id on its call (new nullable column); record mode's synthetic
  check also accepts note scripts.
- The jobs table takes a note job: `message_id` becomes nullable, a `note_id` is added,
  exactly one is set (check constraint), unique on (note, kind). Lease and reaper unchanged.
- Suggestions: Confirm logs one contact (timed at the note's creation), Dismiss logs none.
  The profile merge never overwrites a person-set field.
- No key, kill switch, budget or model down: the note stays, its profile and suggestions
  say "Needs a model key", and the reread pass reads it when the model is back.
- The note eval in replay mode, on fakes in tests. No live call in this phase.

| Check | Maps to | Pass |
|---|---|---|
| A suggestion logs nothing until confirmed; Confirm logs one contact, a second Confirm none; Dismiss logs none | F31 | test |
| The parser drops a suggestion whose quoted words are not in the note | F31 | test |
| Five fixed notes (future, negation, plan, hypothetical, a colleague's call) give no suggestion, in replay against real answers. The notes live in `evals/gold/unit-notes.jsonl` (so record mode accepts them); the test lands in phase 7 with its recordings, since verify allows no failing or skipped test | F31 | test |
| A person-edited profile field survives a model answer | F31 | test |
| No key: note saved, "Needs a model key", no 500 | F31 | test |
| The eval writes BANT F1, key-term recall, authority and month exact, unsupported facts, action precision and recall, each with an interval, from fake answers | F32 | test |

**Phase 6: pages for suggestions.** Confirm and Dismiss on the note, "Needs a model key"
where it applies. Playwright on compose in replay mode against a recorded fixture note.

**Phase 7: the paid runs (only after your go).** A smoke test on the five fixed F31 notes
(recorded, so their test runs in replay), prompt tuning on seed 7, then
seed 11 once with the prompt frozen. Reports committed and reproduced in replay by
`verify.sh`. Floors missed go to `docs/failures/`; ADR-007 closed with the result.

| Check | Maps to | Pass |
|---|---|---|
| Seed-11 report with intervals, committed; replay reproduces it in verify | F32 | verify |
| Every miss in `docs/failures/` with numbers and likely cause | F32 | review |
| Spend from the ledger in the build log, under the cap | F32 | log |

## Spend for the note eval

- **Model:** Claude Haiku 5.5 (`claude-haiku-5-5`), as picked in PR1b, on the Firstcall key
  ($10 key limit; $0.35 spent so far).
- **Per call:** about 1,200 prompt tokens (system prompt, schema, note) and 250 answer tokens:
  1,200 × $0.10/M + 250 × $0.50/M ≈ $0.00025. PR1b measured $0.00022 a call on messages.
- **Runs:** a 5-call smoke test; up to 6 tuning passes on seed 7 (100 scripts each);
  seed 11 once (100 scripts). About 705 calls, **≈ $0.18**.
- **Cap: $0.50 for PR3.** Each run passes `--budget` with what is left; the daily ledger cap
  stays $1.00. If tuning needs a seventh pass, or the total would pass $0.50, I stop and call
  back. No live call happens before your go.

## Decisions the v01 plan leaves open (my recommendation for each)

1. **PR shape:** two PRs, as above.
2. **Who logs a contact:** only the owner, on an owned or nurture lead. Team leads and admins
   add plain notes and edit the profile. This follows "between the owner and the buyer".
3. **When a contact happened:** a typed action is timed when the note is saved; a confirmed
   suggestion is timed at its note's creation, not the tap.
4. **Contact-due clock:** from `owned_since`, once per ownership, stopped by a contact in that
   ownership, so a reassigned lead is watched again. Time to first contact still counts from
   arrival and is set once.
5. **"Behind the cadence":** the latest step whose local date is before today has no touch
   on or after that date. Steps: arrival's local date plus 0, 1, 3, 7, 14, 30 days, then
   every 7 days (weekly; daily 1, monthly 30) after the later of day 30 and the last touch.
   Nurture: 30 days after the later of entering nurture and the last touch.
6. **Follow-up life:** it belongs to the current owner, moves on reassign, is done by any
   touch (`followup.done`), and is closed without an event when the lead leaves owned or
   nurture (no event type exists for that). Next step names the days since the last touch
   and the first missing BANT field.
7. **A missed night:** the next tick runs today's job once; it does not replay each missed
   date, since the job computes from current state.
8. **Lost reasons:** a fixed list (price, location, timing, bought elsewhere, not ready,
   unreachable, other with text), so PR5 can group them; competitor is optional free text.
9. **Delete lead and its event:** delete every row of the lead, its earlier events included,
   and write one `lead.deleted` (lead id and who deleted, no buyer data) into
   `lead_events`, which drops its foreign key so the outbox outlives the lead for PR7.
   Model call rows keep their cost with the message or note link cleared, for the cost page.
10. **Profile merge:** a newer note's model value replaces an older model value; an empty
    model value never clears one; a person-set field is never touched; the profile's notes
    field appends one line per note.
11. **Tidied note:** stored beside the typed text, which stays the record; shown under it as
    "Tidied".
12. **Unsupported facts (the "≤ 1 of 80" floor):** a note counts when any output text holds
    a number not in the script (digits compared after removing `$`, commas and spaces, with
    `k` as thousand), or a capitalised word that is not sentence-first, not in the script
    (case-insensitive) and not a month or weekday. Sentence-first names are missed; the
    report says so.
13. **Scoring:** BANT F1 is micro over the four fields, filled versus empty (authority
    "unknown" is empty). Key-term recall: gold terms found, case-insensitive, in the free-text
    fields. Authority and month exact: over scripts whose gold has a value (false fills
    already count in F1). Intervals: Wilson for rates, a fixed-seed bootstrap of 2,000
    resamples for F1, in plain Python. Actions: precision and recall over (script, kind).
14. **Held-out use:** seed 11 is run once, after the prompt is frozen on seed 7, and
    committed as run. Recordings keep only the answers the committed reports use.
15. **Access matrix for F35:** a new test over the new routes, reusing the F20 harness, so
    the existing matrix test is not edited.
16. **Monitor:** no note, profile or outcome routes, on any lead.
