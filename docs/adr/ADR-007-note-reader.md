# ADR-007: The note reader

- **Status:** Vocabulary, scorer and prediction committed 2026-11-13, with the note gold,
  before any note reader code or model call.
- **Result:** recorded 2026-11-14, from the held-out seed-11 run.

## Context
A salesperson writes a short note after talking to a buyer. One model call turns it into a
tidied note, buyer profile updates and suggested completed contacts. The profile follows
BANT: budget, authority, need and timeline. A suggestion logs nothing until a person taps
Confirm, so a wrong suggestion costs one tap. A wrong profile field or an invented figure
misleads whoever reads the lead next.

## Decision
The reader uses the model PR1b picked, Claude Haiku 5.5, with no model pass of its own. Its
answer has these fields, fixed now so the gold and the schema cannot drift apart
(`firstcall/core/note_terms.py` holds the values):

| Field | Shape |
|---|---|
| `clean_note` | text |
| `budget` | `text`, plus `min` and `max` in whole US dollars, either may be null; a single figure is min = max |
| `authority` | `value`: `decision_maker`, `shares_decision`, `not_decision_maker` or `unknown`; `others`: who else decides, as text |
| `need` | text |
| `timeline` | `text`, plus `month` as `YYYY-MM`, worked out against the note's date |
| `notes` | text |
| `actions` | a list of `kind` (`call`, `text`, `email`, `meeting`) and `quote`, the note's own words that show the contact happened |

A field the note does not support is null. A contact counts in either direction, so an
action has no direction. Only an explicit, completed contact by the note's writer is an
action: future tense, negation, a plan, a hypothetical and a colleague's contact are not.

The reader's code lives in `firstcall/notes/reader.py`. A test checks from git history that
the gold, this record and the scorer were committed before it.

### How a read runs
- **The model is asked outside any lock.** A slow answer must never hold the lead. The reader
  then locks the lead and the note and applies the answer only if the note is still waiting
  and the lead still exists, so a delete or a second reader during the call loses nothing and
  duplicates nothing.
- **An unreadable answer fails the note for good.** Paying again for the same note would
  usually give the same answer. `parse_note_answer` (`firstcall/core/note_read.py`) decides:
  a dollar figure above a trillion, a wrong type, or an authority or action kind outside the
  list is unreadable. A negative budget bound becomes null and a month that is not `YYYY-MM`
  becomes absent. The schema cannot carry a `minimum`, because the API refuses it, so the
  parser enforces it.
- **No model answer is not a failure.** No key, switched off, budget spent or model down leaves
  the note `needs_model`; the scheduler's reread pass reads it later (as for messages, see
  [ADR-006](ADR-006-no-model-fallback.md)).
- **A suggestion needs a click.** A model must not create history, so a suggested contact is
  logged only by Confirm. Its quote must appear in the note, or it is dropped.
- **A person's field always wins.** The merge (`firstcall/board/suggestions.py`) never
  overwrites a person-set field, never clears a field with an empty value, lets a newer note
  replace an older note's value, and takes the budget's minimum and maximum together from
  one note.
- **A confirmed contact closes a follow-up only if it is not older than the follow-up.**
  The follow-up keeps `moved_at`, the time it was last moved to a newer step. Migration 0011
  sets it to the upgrade time for open follow-ups whose due date is later than the UTC date
  they were created on. Comparing UTC dates misses one case: a follow-up created in the
  evening west of UTC (already the next day in UTC) and moved the next morning keeps
  `moved_at` null, so an older contact can still close its newer step.
- **Structured output first.** The transport asks the API for the schema and falls back to
  JSON in the prompt only when the API refuses the schema. Each recorded answer keeps which
  way it was made.

## How it is scored
`firstcall/evaluation/note_score.py`, frozen with the gold:
- **Per field, value-aware:** a filled field is right only when its value matches: budget
  min and max, the authority value, a need term in the need text, and the timeline month. A
  wrong value is one false fill and one miss. BANT F1 is computed per field; each must reach
  0.85.
- **Key terms** count only in their own field, never in the notes or the tidied note. A field
  holding 80% or more of the note's distinct words is a copy and counts no terms.
- **Authority exact and month exact** are separate rates, each at least 0.85, over the
  scripts whose gold has a value.
- **Unsupported facts:** a note counts when the answer holds a number or a person's name the
  note does not. Numbers are read as values on both sides ("$450k", "450,000" and "four
  hundred fifty thousand" are one value). The note and the answer are read by the same
  rules. A number never runs across punctuation, brackets, a slash or a sentence end, so
  "nine hundred thirty thousand; three bedrooms" is two numbers. Each number is the value it
  is written as, so "$450k" never supports 450 million and "two dogs" never supports two
  thousand. A bare number under a thousand joined by "and", "to" or a dash to a scaled one
  takes that scale: "between 380 and 420 thousand" is 380,000 and 420,000 on either side.
  Names come from Faker's en_US name lists, less months, weekdays and names that are
  everyday words. A name outside those lists is missed. At most 1 of 150.
- **Baselines:** "always fill", "never fill" and "copy the note through" miss the floors, and
  the gold written as an answer meets them (tested).
- **Pass rule:** the point estimate decides, with its interval shown. An unreadable answer is
  scored as empty. Floors apply to all seed-11 scripts, seen and unseen phrasing together.
- **Structured output:** the report counts the recorded answers made with JSON in the prompt
  and sets `structured_output` to true only when that count is zero. A report that says
  otherwise is flagged in its Markdown.
- **Held out:** seed 11 is run once, after the prompt is frozen on seed 7. At least 40% of
  its scripts use phrasings seed 7 never has.

## Prediction (pre-registered)
Claude Haiku 5.5, seed 11, all scripts:
1. Budget, authority and need F1 each at least 0.85. Timeline F1 lower, between 0.75 and
   0.90, from months worked out from relative wording ("within six months").
2. Authority exact at least 0.90; month exact between 0.75 and 0.90, for the same reason.
3. Key-term recall at least 0.80.
4. Unsupported facts: 0 to 3 notes, mostly from the tidied note restating a figure in a way
   the note did not.
5. Action precision at least 0.90 and recall at least 0.90. Of the non-action kinds, a
   colleague's contact and a hypothetical draw the most wrong suggestions.
6. Unseen phrasing scores within 5 points of seen phrasing on every rate.

## Result (2026-11-14)

From the [seed-11 report](../../evals/reports/2026-11-14-notes-seed-11.md): 150 scripts, 0
unreadable answers, every call on structured output. The prompt was tuned on seed 7 and
frozen before seed 11 ran (sha256 `93a64f56914201eed01b451b2172d722786c6f24c5c0ae2a734e08197883be59`).

| Measure | Floor | Seed 11 [95% interval] | Met |
|---|---|---|---|
| Budget F1 | >= 0.85 | 1.0 [1.0, 1.0] | yes |
| Authority F1 | >= 0.85 | 1.0 [1.0, 1.0] | yes |
| Need F1 | >= 0.85 | 0.9688 [0.9368, 0.9945] | yes |
| Timeline F1 | >= 0.85 | 0.9385 [0.8889, 0.9778] | yes |
| Key-term recall | >= 0.80 | 0.9902 [0.9717, 0.9967] | yes |
| Authority exact | >= 0.85 | 1.0 [0.9577, 1.0] | yes |
| Timeline month exact | >= 0.85 | 0.9714 [0.9017, 0.9921] | yes |
| Notes with an unsupported number or name | <= 1 | 0 | yes |

Actions, reported with no floor: precision 0.9846 [0.9179, 0.9973], recall 0.8533 [0.7562,
0.9161]. Of the non-action kinds, only one colleague's contact drew a suggestion (14 of 15
clean); every future, negation, plan and hypothetical note drew none. The five fixed F31
notes drew no suggestion, and `tests/test_note_replay.py` replays them.

Predictions:
1. **Budget, authority and need F1 at least 0.85:** held. **Timeline F1 between 0.75 and
   0.90:** did not hold; it came in higher, at 0.9385.
2. **Authority exact at least 0.90:** held, 1.0. **Month exact between 0.75 and 0.90:** did
   not hold; it came in higher, at 0.9714.
3. **Key-term recall at least 0.80:** held, 0.9902.
4. **Unsupported facts 0 to 3:** held, 0.
5. **Action precision at least 0.90:** held. **Recall at least 0.90:** did not hold, 0.8533;
   the colleague rule in the prompt makes the reader pass over some of the writer's own
   contacts. **A colleague's contact and a hypothetical draw the most wrong suggestions:**
   half held; the colleague kind drew the one wrong suggestion, the hypothetical none.
6. **Unseen within 5 points of seen on every rate:** did not hold, the other way round:
   unseen phrasing scored higher on timeline F1 (0.9735 against 0.8788) and month exact
   (1.0 against 0.92).
