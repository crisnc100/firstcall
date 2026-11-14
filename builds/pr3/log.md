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
