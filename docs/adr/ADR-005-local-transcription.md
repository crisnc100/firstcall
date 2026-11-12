# ADR-005: Local transcription and the Whisper size

- **Status:** Prediction committed 2026-11-11, before any transcription code or audio.
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
