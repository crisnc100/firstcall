# 0002: voice memos on seed 11

Run: the voice eval on held-out seed 11, [report](../../evals/reports/2026-11-21-voice-seed-11.md),
[ADR-005](../adr/ADR-005-local-transcription.md). Seed 11's 150 note scripts were read aloud by
Piper in ten voices, once clean and once degraded (noise, a phone band, an Opus round trip,
speed changed by 10%). Each Whisper size transcribed every clip once, and the note reader read
each transcript once with its frozen prompt. The answers are scored against the script the
voice read, so anything Whisper misheard and the reader kept counts as an error.

No size meets every notes floor on degraded audio. Every size runs within the speed bound on
Atlas, so the pick rule ships the largest, `small.en`. These are its misses on degraded audio;
none is a safety floor, so they are published here and do not block the merge.

| Floor | `small.en`, degraded [95% interval] | `small.en`, clean | Typed notes (PR3, seed 11) |
|---|---|---|---|
| Authority F1 >= 0.85 | 0.7976 [0.7111, 0.8765] | 0.9942 | 1.0 |
| Authority exact >= 0.85 | 0.7701 [0.6714, 0.8460] | 0.9885 | 1.0 |
| Notes with an unsupported number or name <= 1 | 23 [15.7, 32.9] | 8 [4.1, 15.3] | 0 |

## 1. Unsupported numbers and names: mostly hearing

The floor counts a note whose answer holds a number or a name that is not in the script.
Names count by sound (`evals/gold/voice/name-spellings.json`), so "Steven" for "Stephen" is no
error; the count by spelling is reported beside it. The gap between the two lines is what
spelling alone costs:

| `small.en` | By sound (other spellings allowed) | By spelling | Spelling only |
|---|---|---|---|
| Clean | 8 | 16 | 8 |
| Degraded | 23 | 30 | 7 |

So on clean audio half of the strict count is a name spelled another way that sounds the
same. The by-sound count is not all hearing either: the scorer checks the reader's answer,
and the reader can add a fact of its own. Each of those notes, split by where its unsupported
numbers and names first appear:

| `small.en` | Notes | All in the transcript | Some in the transcript, some added by the reader | Added by the reader only |
|---|---|---|---|---|
| Clean | 8 | 5 | 3 | 0 |
| Degraded | 23 | 19 | 3 | 1 |

On degraded audio, 22 of the 23 notes carry something Whisper misheard ("seven hundred
eighty-five thousand" written as "$780,000 to $5,000", "four hundred ten thousand" as
"4,110,000"). In the 3 mixed notes the reader also wrote a budget bound of its own, worked
out from the misheard figure. The one note from the reader alone is `note-11-113`,
where the transcript is right and the reader added a date to its notes. A misheard figure is
one the salesperson would see, so the scorer checks the answer against the script, as the
plan chose.

Two effects of how the audio was made, kept as they are:
- A figure such as 705,000 is spoken "seven hundred five thousand", with no "and", and Whisper
  can hear it as 700,000 and 5,000. Five seed-11 scripts have one; `small.en` heard three of
  them wrong on degraded audio and one on clean. A salesperson can say it the same way.
- Whisper sometimes writes "e-mail", which word error rate counts as two errors against
  "email". Word error rate has no floor.

## 2. Authority: a relative clause misheard

On degraded audio 20 notes get the wrong authority, against 1 on clean audio. In 13 of the 20
the sentence is "Buying for his (or her) parents, who make the final call", heard as "… to
make the final call", "he makes the final call" and the like. The reader then took 11 of those
as a shared decision or the buyer's own, and left 2 as unknown. Who decides rides on one or
two short words, and those are what the degraded audio loses.

## What it costs

The smaller sizes miss more: `base.en` meets 2 of 8 floors on degraded audio and `tiny.en`
none. On clean audio every size meets every floor but the unsupported one. Word error rate
for `small.en` is 6.35% clean and 15.43% degraded.

## v0.2 items

- Show a voice note's figures and names for the salesperson to check before the profile takes
  them, since that is where a mishearing lands.
- Measure voice on real browser recordings. The degraded clips are a deliberate worst case
  (phone band, 12 kbps Opus, noise); how close real memos come to either set is unmeasured.
- Time `medium.en` on the beta host: `small.en` uses about a third of the speed bound.
