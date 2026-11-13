# ADR-007: The note reader

- **Status:** Vocabulary, scorer and prediction committed 2026-11-13, with the note gold,
  before any note reader code or model call.
- **Result:** not yet recorded.

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
  hundred fifty thousand" are one value). Names come from Faker's en_US name lists, less
  months, weekdays and names that are everyday words. A name outside those lists is missed.
  At most 1 of 150.
- **Baselines:** "always fill", "never fill" and "copy the note through" miss the floors, and
  the gold written as an answer meets them (tested).
- **Pass rule:** the point estimate decides, with its interval shown. An unreadable answer is
  scored as empty. Floors apply to all seed-11 scripts, seen and unseen phrasing together.
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

## Result
Not yet recorded.
