# ADR-005: Local transcription and the Whisper size

- **Status:** Prediction committed 2026-11-11, before any transcription code or audio. The
  candidates, settings, speed bound and pick rule were fixed on 2026-11-21 and committed with
  the seed-11 voice gold, before any transcription code.
- **Result:** pending until the voice eval runs.

## Context
Salespeople log calls by voice memo. A hosted speech-to-text service costs money per minute
and sends audio off the machine. faster-whisper, an open-source Whisper, runs locally in the
worker with no key.

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
and the faster-whisper version. They shaped the spoken-text rule. No seed-11 audio has been
transcribed.
