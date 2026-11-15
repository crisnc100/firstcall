# PR3 build log

## Phase 1: note gold, ADR-007 and the frozen scorer

- **Where the gold lives.** `evals/gold/notes/seed-{7,11}/{scripts,labels}.jsonl`, with its
  own manifest, `evals/gold/notes/MANIFEST.json`. The plan said "manifest entries"; the
  message manifest test pins each seed folder to exactly three files, so a separate folder
  and manifest keep every existing test and gold file unchanged.
  `firstcall corpus notes --seed N` writes them.
- **Scripts.** 150 per seed: 60 with a completed contact (15 with two kinds), 15 of each hard
  non-action kind, 15 with neither. Half the action scripts also carry one non-action
  sentence, so a reader has to tell them apart inside one note. Each BANT field is present in
  87 of 150 (42% empty). Budgets come in five seen number styles (`$450k`, `$450,000`,
  `450 thousand`, words, `$1.2 million`) and three unseen ones (`450K`, `450 grand`,
  `$1.25M`).
- **Held-out phrasing.** In seed 11 each sentence takes an unseen phrasing or number style
  with a 20% chance; 95 of 150 seed-11 scripts have at least one. Seed 7 has none, and a test
  checks that each unseen phrasing's fixed words never appear in seed 7.
- **Gold the script supports.** Every key term, budget figure (read by the scorer's own
  number reader) and month is checked against its script. Some notes never name the buyer, so
  `buyer` is null there (21 scripts in each seed).
- **Scorer** (`firstcall/evaluation/note_score.py`), as the plan states. Two choices made
  while writing it, plus one limit:
  - Word numbers are read only outside digit numbers, so "1.5 million" is not also one
    million, and "and" joins word numbers only after "hundred" ("four hundred and fifty"),
    so "430 thousand and 505 thousand" stays two numbers.
  - The name list is Faker's en_US first and last names less months, weekdays and 150
    names that are everyday words (`EVERYDAY_NAMES`), so "Price", "Park" or "Cash" in an
    answer is not an invented person.
  - A field is a copy when it holds 80% of the script's distinct words; on a very short note a
    short field can trip it, which the note scripts (two to seven sentences) do not reach.
- **Order check.** `test_note_gold_and_the_scorer_were_committed_before_any_reader_code`
  reads git history for `firstcall/notes/reader.py`, where ADR-007 says the reader lives.
- **Baselines.** Always fill, never fill and copy through each miss the floors on both seeds;
  the gold written as an answer meets every floor with zero unsupported notes.
- **Ruff.** `S311` is ignored for `note_score.py` in pyproject: the bootstrap uses a seeded
  `random.Random` so the report reproduces.
- verify.sh: 652 passed; `note_score.py` branch coverage 98%.

## Phase 2: notes, contacts, outcomes and delete (backend)

- **Migration 0009.** `notes`, `contacts` (unique on note and kind), `lead_profiles` and
  `outcomes`, plus lead columns for first contact, `uncontacted_since` and `deleted_at`.
  Leads already owned start their uncontacted stretch at `owned_since`. A trigger makes
  `outcomes` append-only: it refuses UPDATE always, and DELETE unless the lead is marked
  deleted, which delete lead does first in the same transaction.
- **Who.** `core/outcomes.py`: notes and contacts by the owner, a team lead of the lead's
  team, or an admin; contacts only on owned or nurture leads (422 otherwise, since the person
  may note the lead). Notes, contacts, profile and outcomes are read by the lead's team and
  admins; a monitor's lead detail carries none of those keys.
- **Work actions.** The lead JSON keeps `actions` for the board moves and adds `work` for
  note, log_contact, set_nurture, resume, set_outcome, reopen and delete, so PR2's tests that
  pin `actions` are unchanged.
- **Contact time.** A typed action is timed when the note is saved. First contact is the
  earliest contact, worked out again when an earlier one is logged, and never before arrival.
- **Contact-due clock.** `uncontacted_since` is set on a claim when empty and on every reopen,
  and cleared by any contact at or after it. Levels `contact_owner` (240) and
  `contact_team_leads` (300) fire once per stretch, in business minutes, only while the lead is
  owned. Teams can override both minutes in `teams.escalation`. The escalation log marks a
  contact level current while the same stretch stands.
- **Outcomes.** Lost reasons are the fixed list in `core/outcomes.py`; "other" needs text; a
  competitor is kept only for lost. Event payloads carry the reason code, never the text or
  competitor. Nurture writes an outcome row; resume writes `lead.reopened` with reason
  `resumed`. Admins may reopen but not set outcomes, as the transition table says.
- **Buyer writes again** (`pipeline/process.py`). A nurture lead goes back to owned in its one
  `lead.activity` event (`to: owned`). A lost or not-real lead reopens on a lead-category
  message that is not an auto-reply (`Auto-Submitted` other than `no`), with its own event
  cause `message:<id>:reopen`. A closed lead whose owner is inactive or out of the team
  alerts the team leads, or reopens to the pool. The owner's user row is share-locked before
  the lead row, the order PR2 set for every path.
- **Delete lead** (`board/delete.py`). As the plan says. It takes the lead's identity locks
  before the lead row, as matching does; a message whose attach target was deleted while it
  waited raises and retries, and its retry finds no match. Inbox emails are found by their
  links, other leads' duplicate flags by their JSON text.
- **Tests.** `test_notes_contacts.py`, `test_outcomes.py`, `test_delete_lead.py` (every table
  classified; an unclassified one fails) and `test_note_access.py` (the new access matrix,
  the monitor's detail, and an allowlist of event payload keys holding ids and codes only).
  No existing test changed.

## Phase 3: cadence and the nightly job

- **Migration 0010.** `followups` (a partial unique index allows one open follow-up per
  lead), `followup_runs` (unique on team and local date) and `teams.cadence` (empty means the
  default).
- **Cadence** (`core/cadence.py`, pure). Steps on arrival's local date plus 0, 1, 3, 7, 14
  and 30; then the steady rhythm (daily 1, weekly 7, monthly 30) from the later of the last
  step and the last touch; nurture monthly from the later of entering nurture (its latest
  nurture outcome) and the last touch. A step on a closed day moves to the next open day;
  when that puts two steps on one date, the later step names the follow-up.
- **Due and on time.** The job opens a follow-up for the latest step whose date has come and
  that no touch met (a touch on or after the step's date meets it). `due_on` is that date in
  the team's time; the lead JSON shows `overdue` once the team's today is past it, and My
  leads lists open follow-ups first, oldest due date at the top.
- **Life.** A contact closes the open follow-up (`followup.done`). Release, return, owner
  left, move team and a closed outcome cancel it (`followup.cancelled`, with the state it
  went to). Reassign hands it to the new owner. Nurture keeps it. Delete lead deletes it.
  `docs/definitions.md` gains `followup.cancelled` and the due-date rule (Cristian's D1).
- **The run.** `run_nightly` runs each team once its local time reaches 05:00 and no run row
  exists for its local date, so a missed night is one run the next tick. The run row and every
  change commit in one transaction; each lead runs in a savepoint and a lead that raises is
  logged and counted in `errors`. A team whose hours or cadence do not read is skipped and
  counted. The failpoint `during_followup_run` fires after each lead.
- **Where it runs.** A new `followup_tick` in the scheduler loop and `firstcall tick`, kept
  apart from `tick` and `alert_tick` so their pinned results stay the same.
- **Tests.** `test_cadence.py` (pure) and `test_followups.py`: the 05:00 start, a double run,
  a missed night, one open follow-up, a touch on time, overdue and My leads order, cancel on
  release and on an outcome, reassign, a team cadence and a broken one, a lead that raises,
  zero gateway calls, spring forward and fall back, steps across the change, Pacific/Auckland
  (local date ahead of UTC), and a killed run (exit 137 and the log line) redone whole.
- **Two phase-2 tests changed**, in their own commit: the delete test classifies the two new
  tables and checks a follow-up is deleted; the event payload test allows and expects the
  three follow-up events.
- verify.sh: 754 passed.

## Phases 1-3 review fixes (reviews 917b21 and d3dd8b)

Nine MEDIUM findings, no HIGH, each with a failing test first in `tests/test_pr3a_review.py`.
- **Seed keeps a team's cadence** (917b21 f1). `seed.py` copies and checks `cadence` beside
  hours and escalation; a bad one stops the seed.
- **A deleted lead's messages leave the reread queue** (f2). The message scrub sets
  `needs_reread` false. Reread also stops when the lead it locks has been deleted, so a model
  answer read before a delete never refills the tombstone.
- **An earlier message moves time to first contact** (f3). When an attach moves `arrival_at`
  back, `notes.first_contact_times` recomputes both durations from the new arrival.
- **My leads orders before its limit** (f4). Due follow-ups first, oldest due date, then
  arrival, all in the query.
- **Rhythm steps across a closed day** (f6). If the newest steady step moves past today, the
  latest earlier one whose open day has come stands.
- **Owner left skips tombstones** (f7). The walk test sends every lead write route to a
  deleted owned and a deleted nurture lead (all 404), runs escalate and the nightly job, and
  removes and deactivates the owner: no event, state, alert or follow-up changes.
- **Gold** (d3dd8b f1, f2, and 917b21 f5): corner lot is a need; labels record the number
  style written, so 5 seed-11 scripts become seen (60 seen, 90 unseen); the scorer scales a
  number only by a scale the script writes. Script text is unchanged; labels regenerated;
  manifest, gold CHANGELOG and ADR-007 updated. No note reader or model run exists yet.
- verify.sh: 767 passed, every floor met.

## Phase 4: pages

Plain, logic first; the redesign comes later. Every buyer and note string goes in with
textContent.
- **Lead page** (`web/src/pages/work.ts`, wired into `detail.ts`): the time to first contact
  in business and clock minutes; the open follow-up (due or overdue since, and its next-step
  line); the notes, each with the contacts it logged; a note form whose "This note records"
  select (Called, Texted, Emailed, Met) appears only when the viewer may log a contact; the
  buyer profile and an editor that sends only the fields the person changed, so the rest stay
  open to the model.
- **Actions column**, under More actions: Mark won, Mark lost (reason, other text,
  competitor), Mark not real, Move to nurture, Resume, Reopen, and for admins Delete lead,
  behind a second confirm, which goes back to the list. Each shows only when the lead's
  `work` list allows it.
- **History** reads the new events as sentences (note added, contact logged, outcome with
  reason, reopened and why, follow-up due, done and closed).
- **Lists**: a follow-up due or overdue badge, beside the possible-duplicate badge.
- **Test**: `tests/fault/test_pages_notes.py` on compose in Chromium. The owner claims, adds a
  note logging a call and sees the contact badge, the first-contact line and the history
  line; a note holding a script tag shows as text and never runs; the owner marks it lost
  for timing; the team lead reopens it to its owner; the admin deletes it and leaves the
  page, and the row is a scrubbed tombstone.
- verify.sh: 768 passed, every floor met.

## Fix check and phase 4 review fixes (917b21 x1, 13ee35 f1)

- **Scorer numbers** (x1). Each script number supports only its written value, after
  `read_numbers` expands its own suffix: "$450k" no longer supports 450 million, and "two dogs"
  beside "$450k" no longer supports two thousand. A bare number joined by "and", "to" or a dash
  to a scaled one shares that scale, so "between 380 and 420 thousand" supports 380,000. The
  baseline floors test passes unchanged. ADR-007 and the gold CHANGELOG say so; no gold file
  changed.
- **Follow-up badges refresh** (f1). The item line and its badges are rebuilt on every list
  refresh. The page test opens a follow-up, sees its badge on My leads, closes it in the
  database and waits for the poll to remove it; that check failed on the old list code.
- verify.sh: 770 passed, every floor met.

## Number reader, made structural (review 1c69e8 f1, f2)

The third round on the scorer's numbers, so one reader replaces the patches.
- **One reader for both sides.** `read_numbers` reads the script and the answer alike; the
  script-only helper is gone. It splits the text at ; : ! ? line breaks, em dashes, and any
  comma or full stop outside a figure, so no number runs across punctuation or a sentence
  end. Digits and number words are read in one token pass per clause. A bare number under a
  thousand joined by "and", "to" or a dash to a scaled number takes that scale, on either
  side: an answer's "between 425 and 450 thousand" is supported.
- **Regression table** (`tests/test_note_numbers.py`): 25 faithful rewrites of gold scripts
  from both seeds (10 ranges or more, three or more in number words) state nothing
  unsupported; 10 invented numbers (wrong scale, wrong digits, from nowhere) are flagged
  exactly; every script in both seeds, copied through and with its budget restated in
  figures from the gold, is supported; and punctuation cases read as separate numbers. On
  the old reader 18 of these fail.
- The baseline floors test passes unchanged. ADR-007 and the gold CHANGELOG say so; no gold
  file changed.
- verify.sh: 816 passed, every floor met.

# PR3b (branch note-reader, stacked on notes-followups)

## Deferred fix-check items x1 and x2

- Test-only commit first (4c6ff44): the number table gains brackets, a slash and
  half-a-million, failing on the old reader. The fix (edc8a4a) ends a number at brackets and
  slashes and reads a hyphen between letters as a space. ADR-007 and the gold CHANGELOG
  record it; no gold file changed.

## Phase 5: the note reader (no key)

- **Answer** (`core/note_read.py`, pure): the schema from the ADR-007 vocabulary and a parser
  that never raises. A suggested action whose quote is not in the note is dropped; a wrong
  type, an authority or a kind outside ADR-007 makes the answer unreadable; a month that is
  not YYYY-MM reads as absent.
- **Prompt** `read_note`: a fixed system prompt; the note goes only in the user message,
  fenced in `<note>`, with its team-local date and the team's time zone as context.
- **Gateway:** requests and `model_calls` carry a nullable `note_id`. Note answers are
  recorded in their own file, `notes-<model>.jsonl`. Record mode also accepts note scripts and
  `evals/gold/unit-notes.jsonl` (the five F31 notes and the page fixture note).
- **Migration 0011:** note read state (pending, read, needs_model, failed), reason, tidied
  note, model answer; `note_suggestions` (unique per note and kind); jobs take a note or a
  message, never both, with their own creation time; `lead_profiles.model_from`.
- **Note jobs:** saving a note queues `read_note`. `claim` no longer joins messages and orders
  by the job's creation time; `run` dispatches by subject; an exhausted note job fails its note
  with `retries_exhausted`, keeping the text and any typed action. The reader lives at
  `firstcall/notes/reader.py`, the path ADR-007 names, so the gold-before-reader test covers
  it. The model is asked before the lead is locked; the answer is applied under the lock only
  if the note still waits and the lead is not deleted.
- **No model:** no key, kill switch, budget or model down leave the note `needs_model` with
  the reason; the scheduler's tick rereads those notes, stopping at the first refusal. An
  unreadable answer fails the note so it is never paid for again.
- **Suggestions** (`board/suggestions.py`): Confirm logs one contact dated at the note and
  made by its author, by the owner, a team lead of the team or an admin; others get 404.
  Confirm on a lead not owned or in nurture is 409 until reopened; a second Confirm is 409.
  A kind the note already logged closes as already logged. A reassigned lead's suggestions
  go to whoever may now confirm them.
- **Profile merge:** never a person-set field, never an empty value, never over a newer
  note's value; each note's other facts add one line to Notes.
- **Delete** removes suggestions and note jobs and keeps model calls without their note.
- **Eval runner** (`evaluation/note_eval.py`, `firstcall eval-notes`): reads every script
  through the gateway in a throwaway database, scores with the frozen scorer, adds the
  baselines and the call log, and writes no report for an incomplete run. Tested record then
  replay on fake answers.
- **Tests:** `test_note_read.py`, `test_note_reader.py`, `test_note_eval.py`. Two existing
  tests changed, in their own commit: the access matrix adds confirm and dismiss, and the
  delete test classifies `note_suggestions`.
- verify.sh: 861 passed, every floor met.

## Phase 6: pages for suggestions

- **Lead page** (`web/src/pages/work.ts`): each note shows its read state ("Reading this
  note…", "Needs a model key", "Couldn't read this note"), the tidied note, and each suggested
  contact with its quote. An open one has Confirm and Dismiss for whoever may write notes;
  a closed one shows confirmed, dismissed or already logged. Text goes in with textContent.
- **Test** `tests/fault/test_pages_suggestions.py` on compose in Chromium: a worker with no
  key leaves the owner's note at "Needs a model key"; one `firstcall tick` in replay mode
  reads it from a fixture answer the test writes, keyed to the exact request for the
  `unit-page` note; the owner sees the tidied note and the suggested call, confirms it, and a
  suggestion-sourced contact is logged. The fixture answer is hand-written, not a model's.
- **F31 unit notes:** `firstcall eval-notes --unit` reads the five fixed notes once each and
  prints what each suggests; tested record then replay on fakes. Their replay test lands with
  the phase 7 recording.
- verify.sh: 863 passed, every floor met.

## Phases 5-6 review fixes (72756c, 053866)

Merged origin/main (PR3a, squashed as 362c632) into note-reader first; main's tree equals
PR3a's tip, so every conflict took the branch's side and the merge changed no file.
Each fix has a failing test first: `tests/test_note_reader_fixes.py` (11 of 12 failed
before the fixes) and `tests/fault/test_pages_reader_fixes.py` (failed on the old page).
- **Pages f1:** profile Notes is a textarea, so its line breaks survive and an edit to
  another field never sends Notes. The page test checks only `need` becomes person-set.
- **Pages f2:** while a note is pending or waiting for a model, the lead page fetches again
  every 15 seconds and redraws only when a read changed and nothing is being typed or open.
- **f1:** budget bounds merge as a pair from one note; a person-set bound keeps the model
  off both.
- **f2:** migration 0011 moves notes written before the reader to `needs_model`
  (`written_before_reader`), so the reread pass reads them.
- **f3:** a contact closes a follow-up only if it happened after the follow-up opened or on
  or after its due date in the team's time.
- **f4, s1:** each note in the reread pass has its own error boundary: a replay miss halts
  the pass, any other error is logged, counted and fails that note. `schedule()` survives a
  failing tick. Budget figures above $10^12 make an answer unreadable; NUL characters are
  stripped from answer text.
- **f5:** `record_and_report` keeps the record pass's calls, failed attempts and ledger
  spend in the report (`record_pass`), beside the replay's reproducible figures.
- **f6:** a call whose note was deleted mid-read is logged without the note, cost kept.
- **f7:** a note job holds no job lock while the model reads and takes the lead before its
  own row, the order delete uses; a job deleted meanwhile ends quietly. A threaded test
  deletes the lead while the model call is in flight.
- f8 (LOW, downgrade) is filed, not fixed.
- verify.sh: 876 passed, every floor met.

## Phase 7: the paid runs (2026-11-14)

Model `claude-haiku-5-5`. Each run used a throwaway database, so its ledger is that run's
spend. Tuning passes recorded into scratch folders; only the frozen prompt's answers are
committed.

| Run | Prompt sha256 (first 12) | Calls | Failed | Fallback calls | Ledger $ |
|---|---|---|---|---|---|
| Smoke, 5 seed-7 scripts | 26b892e846cf | 5 | 0 | 5 (schema refused) | 0.000761 |
| Smoke after the schema fix | 26b892e846cf | 5 | 0 | 0 | 0.001231 |
| Seed 7, pass 1 | 26b892e846cf | 150 | 0 | 0 | 0.039338 |
| Seed 7, pass 2 | eba6c21f50fa | 150 | 0 | 0 | 0.040415 |
| Seed 7, pass 3 (frozen) | 93a64f569142 | 150 | 0 | 0 | 0.041525 |
| Seed 7, pass 4 (discarded) | 16fa33443382 | 150 | 0 | 0 | 0.041575 |
| Seed 11, once | 93a64f569142 | 150 | 0 | 0 | 0.041673 |
| Five F31 unit notes, once | 93a64f569142 | 5 | 0 | 0 | 0.001121 |
| Unit-page note, once | 93a64f569142 | 1 | 0 | 0 | 0.000271 |
| **Total** | | 766 | 0 | | **0.207910** |

The cap was $0.50. The unit-note figures are the sums of the recordings' costs; the rest are
the record passes' ledgers.

- **Schema:** the first smoke run showed the API refusing the answer schema over `minimum`
  on the budget bounds; the transport fell back to JSON in the prompt. Fixed before tuning
  (cb06c3a): no `minimum`, a negative bound parses as null, and the eval counts fallback
  calls and flags any run with one.
- **Tuning:** pass 1 missed key-term recall (0.7796): budget text left out how the buyer pays
  and timeline text its hedge. Pass 2 asked for both and met every floor, but 10 of 15
  colleague notes drew a suggestion. Pass 3 named the writer's own contacts (no subject, or I
  and we) against a named colleague's: colleague 10 of 15 clean, precision 0.9342, recall
  0.9467. Pass 4 pushed the colleague rule further: colleague 12 of 15, but recall fell to
  0.8533 and a future note drew a suggestion, so it was discarded.
- **Frozen prompt:** sha256 `93a64f56914201eed01b451b2172d722786c6f24c5c0ae2a734e08197883be59`.
- **Seed 11:** every floor met; see ADR-007's result. No floor was missed, so
  `docs/failures/` has no new entry.
- **F31:** the five unit notes drew no suggestion; `tests/test_note_replay.py` replays them
  and the unit-page note. The page test replays the real unit-page answer, dated to the unit
  notes' date, in place of the hand-written fixture.
