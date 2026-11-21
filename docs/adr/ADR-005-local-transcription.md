# ADR-005: Local transcription and the Whisper size

- **Status:** Prediction committed 2026-11-11, before any transcription code or audio. The
  candidates, settings, speed bound and pick rule were fixed on 2026-11-21 and committed with
  the seed-11 voice gold, before any transcription code.
- **Result:** recorded 2026-11-21, from the seed-11 voice eval. `small.en` ships.

## Context
Salespeople log calls by voice memo. A hosted speech-to-text service costs money per minute
and sends audio off the machine. faster-whisper, an open-source Whisper, runs locally in a
transcriber process of its own, with no key.

## Decision
Transcription runs locally with faster-whisper. The eval picks the smallest Whisper size that
meets the note floors on degraded audio (several voices and accents, background noise at
10 dB signal-to-noise, a phone-band filter, speed changed by 10%). The audio is deleted right
after transcription.

## Prediction (pre-registered)
1. The `small` size is the smallest that meets the note floors on degraded audio; `base`
   misses at least one floor.
2. Word error rate on clean audio is under 8% for `small`, and under 20% on degraded audio.
3. `small` runs at under 60 compute seconds per audio minute on the beta host's CPU.

## Candidates and settings (fixed 2026-11-21, before any result)
- **Candidates:** `tiny.en`, `base.en` and `small.en`, the English-only builds of the sizes
  the prediction names. The demo is in English, and an English-only build is more accurate
  than the multilingual one of the same size.
- **Settings, the same for every candidate and in the product:** `int8` on the CPU,
  2 threads, language `en`, beam 5, the voice activity filter on (it skips silence, where
  Whisper invents text), and no conditioning on earlier text (it stops repeat loops).
- **Speed bound:** 60 compute seconds per audio minute on the beta host, limited to 2 CPUs,
  as prediction 3 states.
- **Pick rule:** the smallest size whose degraded run meets every notes floor and runs within
  the speed bound. If none does, the largest size within the speed bound ships and each miss
  goes to `docs/failures/`. Point estimates decide, as in every earlier eval.
- **Until the eval runs,** the product defaults to `small.en`, the size prediction 1 names.

## How voice is scored
- **The audio** is seed 11's 150 note scripts read by Piper in ten licensed voices, among
  them UK, Irish, Scottish and Australian English, once clean and once degraded as the
  decision above describes (`evals/gold/voice/`, with its sources in `LICENSES.md`). Each
  figure is spoken as its full value in words ("four hundred twenty-five thousand dollars"),
  so the audio carries no number style.
- **Scored against the script,** not the transcript: the transcript goes through the note
  reader with its frozen prompt, and the frozen note scorer checks the answer against the
  script the voice read, so a misheard figure counts as an error.
- **Names by sound** (approved by Cristian on 2026-11-21). No transcriber can hear how a name
  is spelled, and the note floor allows one note in 150 with a name the script does not hold.
  For voice only, a name counts as supported when the script spells it another way that
  sounds the same, from a fixed table of groups (`evals/gold/voice/name-spellings.json`):
  42 of seed 11's 150 scripts hold such a name. The strict count, by spelling, is reported on
  its own line. Numbers stay strict.
- **Word error rate** is reported with no floor: lower case, no punctuation, and each number
  read by the note scorer's reader, so "$425k" and "four hundred twenty-five thousand" are
  the same word. Punctuation inside a word is dropped too ("H.O.A." is "HOA"), and two spoken
  forms that reader leaves as words are read as values for WER only: "one point one million"
  and "seven hundred five grand".

## Disclosure
Before the candidates and settings were fixed, two hand-written clips of the `unit-page` note
(`evals/gold/unit-notes.jsonl`) ran through all three sizes on a Mac, to check the pipeline
and the faster-whisper version. They shaped the spoken-text rule. A dry run on 30 degraded seed-7 clips
followed and changed nothing. Seed-11 audio was first transcribed in the eval itself, once
per size and condition.

## Result (2026-11-21)

From the [seed-11 voice report](../../evals/reports/2026-11-21-voice-seed-11.md): 150 scripts
per size and condition, each transcript read once with the note reader's frozen prompt
(sha256 `93a64f56914201eed01b451b2172d722786c6f24c5c0ae2a734e08197883be59`), every read on
structured output.

| Size | Degraded floors met | WER, clean / degraded | Unsupported notes by sound (by spelling), clean / degraded | Atlas compute s per audio minute |
|---|---|---|---|---|
| tiny.en | 0 of 8 | 0.1224 / 0.3105 | 20 (25) / 40 (41) | 4.80 |
| base.en | 2 of 8 | 0.0936 / 0.2165 | 16 (24) / 27 (35) | 7.37 |
| small.en | 5 of 8 | 0.0635 / 0.1543 | 8 (16) / 23 (30) | 20.73 |

**`small.en` ships.** No size meets every notes floor on degraded audio, and every size runs
within the speed bound on Atlas (an AMD EPYC 9354P, the voice image limited to 2 CPUs), so
the pick rule ships the largest. On degraded audio it misses authority F1 (0.7976
[0.7111, 0.8765]), authority exact (0.7701 [0.6714, 0.8460]) and notes with an unsupported
number or name (23 of 150, the floor at most 1). The misses are published in
[docs/failures/0002](../failures/0002-voice-seed-11.md). On clean audio `small.en` meets every
floor but the unsupported one, at 8.

Predictions:
1. **`small` is the smallest size that meets the note floors on degraded audio:** did not
   hold; no size meets them. **`base` misses at least one floor:** held, it misses six.
2. **`small` WER under 8% clean and under 20% degraded:** held, 6.35% and 15.43%.
3. **`small` under 60 compute seconds per audio minute on the beta host:** held, 20.73.
