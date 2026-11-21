# Firstcall PR4: voice (build plan, rev 2)

Source: `builds/v01/plan.md`, "PR4: voice" (F36 to F40), with the definitions it relies on:
note scripts, voice memos, the notes floors, the missed-floor rule, seed 11, the no-model
fallback and model modes. Branch `voice`, cut from main at 3107006. Rev 2 answers the
advisor's Bounce (`builds/pr4/bounce.md`). Each item was checked against the code first; the
last section maps all 20 to their answers.

Terms used below:
- **Voice memo:** a short recording a salesperson makes about a lead. It becomes a note.
- **Whisper size:** Whisper comes in sizes. Bigger is more accurate, slower and uses more
  memory. `.en` sizes know only English, which suits an English demo.
- **WER (word error rate):** the share of words a transcript gets wrong.
- **Clean / degraded clip:** a memo rendered by text-to-speech as is, and the same memo made
  harder with noise, a phone-band filter, a lossy codec and changed speed.
- **Replay:** answers saved from a real run and read back, so tests need no model, key or
  Whisper. A missing answer raises.
- **Compose profile:** a tag on a compose service. Plain `docker compose build` and `up`
  skip a service that has one, unless that profile is named.

## Outcome

A salesperson records a memo on a lead's page, or picks an audio file, and may pick the
contact it records (Called, Texted, Emailed, Met), as on a typed note. The api refuses a
wrong format (415), over 5 MB (413) or over 3 minutes (422) before storing anything. The note
appears at once as "Transcribing…", and its contact, if picked, is logged at once. A
transcriber process turns the audio into text with faster-whisper, locally, with no key. The
transcript fills the note, and PR3's reader reads it like a typed note. The audio is deleted
when transcription ends, fails, or has waited 30 minutes. The voice eval runs seed 11's
scripts as audio through each candidate size and ships the smallest that meets the notes
floors on degraded audio and runs fast enough on the beta host. PR CI never runs Whisper,
never downloads a model and never builds the voice image.

## Out of scope

- The dashboard's voice share (PR5). PR4 stores each note's source, typed or voice.
- Hosting and the beta's limits (PR6). PR4 times Whisper on Atlas once (decision 5) and
  deploys nothing.
- CRM events (PR7).
- Tuning the reader's prompt on voice. The PR3 prompt stays frozen (`builds/pr3/log.md:320`).
- Keeping audio for playback, languages other than English, live transcription.

## Order: a CI PR first, then two voice PRs

- **CI first (decision 1):** Firstcall CI is one job running all of `verify.sh`
  (`.github/workflows/verify.yml:41-42`); the last main run took 13.5 minutes (run
  44320487195). A small PR gives it Docket's shape on main: `verify.sh --step`, parallel
  lint, eval and sharded test jobs (pytest-split), a browser job, and the compose fault
  tests only on main, nightly and manual runs. PR4a lands on that layout.
- **PR4a, the voice path (no spend):** the seed-11 audio, upload and limits, storage and
  deletion, the transcriber, the page, the image. Covers F36, F37, F39 and F40's first half.
- **PR4b, the eval and the size choice:** the seed-7 dry run, the seed-11 runs, the Atlas
  timing, ADR-005 closed. Covers F38 and F40's second half.

Two voice PRs, as PR3 split (`builds/pr3/plan.md:33-51`): every paid call and every Whisper
run that decides something sits in one small PR. 4a ships `small.en`, ADR-005's predicted
size, as the default; 4b changes it only if the eval says so.

## Plan versus code

1. **300 clips, not 160.** v01 assumed 80 scripts (`builds/v01/plan.md:326-332`); PR3 built
   150 (`builds/pr3/log.md:10`). Seed 11's scripts hold 4,320 words; at about 3 KB a second
   (24 kbps, measured on two clips) that is roughly 9 MB, under v01's 15 MB check.
2. **piper-tts is GPL-3.0.** v01 puts it in the dev group (`builds/v01/plan.md:60,68`);
   piper-tts 1.8.0 declares `GPL-3.0-or-later` (checked). It runs as a one-off tool
   (`uv run --with piper-tts==1.8.0`), outside `pyproject.toml` and `uv.lock`.
3. **No ffmpeg in the image** (`Dockerfile:8-20`), though v01 expects it
   (`builds/v01/plan.md:61`). 4a adds it.
4. **One worker runs every job in turn** (`firstcall/pipeline/worker.py:113`;
   `claim` takes any kind, `jobs.py:64-70`). Whisper "in the worker"
   (`builds/v01/plan.md:767`) would hold new-lead alerts behind a memo and break the
   2-second floor (F28). Decision 2.
5. **faster-whisper 1.2.1 fails with av 19.0.2** (measured: `TypeError … 'metadata_errors'`).
   av 18.1.0 works. The lock pins `av<19`.
6. **"Run once" and "pick the smallest size".** v01 runs the voice confirm once
   (`builds/v01/plan.md:407`) and picks the smallest passing size
   (`builds/v01/plan.md:685-686`). Here each (size, condition) pair runs exactly once.
7. **F37 "reads the disk".** Audio waits in Postgres (decision 3), so the test reads the
   audio table.
8. **No multipart parser** (`pyproject.toml:8-23`). The browser sends the audio as the raw
   body; the existing capped reader (`firstcall/intake/limits.py:66-75`) stops at 5 MB.
9. **`data/LICENSES`** (`builds/v01/plan.md:69`) has no folder; it becomes
   `evals/gold/voice/LICENSES.md`, beside the audio.
10. **Notes are written once** (`firstcall/db.py:448`). A voice note is created empty at
    upload and filled once by the transcriber. Nothing else edits a note.

## How it works

### Recording and upload

- **MediaRecorder** (the browser's recording API), mono, echo cancelling and noise
  suppression on, 32 kbps. First supported format wins: `audio/webm;codecs=opus` (Chrome,
  Edge, Firefox), `audio/mp4` (Safari, AAC), `audio/ogg;codecs=opus`. Which Safari versions
  record WebM is unverified.
- A timer, and the recorder stops itself at 3:00. A file picker sits beside it.
- The same "This note records" select as the typed note form (`web/src/pages/work.ts:170-188`).
- `POST /api/leads/{id}/voice?action=…`: the audio as the raw body, its type in
  `Content-Type`, the CSRF header. `send()` (`web/src/api.ts:169`) sends JSON only, so a
  raw-body variant is added.
- The page polls already while a note is being read (`web/src/pages/detail.ts:170-215`);
  `waiting` (`detail.ts:197`) and `reads` gain the `transcribing` state, so the transcript
  appears without a reload.
- The microphone needs HTTPS outside localhost (PR6).

### Limits, in the api, before anything is stored (F36)

All of this runs in the threadpool, as intake does (`firstcall/api.py:101,143`), so the
event loop never waits on ffmpeg.
1. **413:** `Content-Length` over 5 MB, or a body that passes 5 MB while read.
2. **415, magic bytes:** WebM (`1A 45 DF A3`), MP4 (`ftyp` at byte 4), WAV (`RIFF`…`WAVE`),
   MP3 (`ID3` or a frame sync) or Ogg (`OggS`). The declared type is not trusted.
3. **415, ffprobe:** on a temporary file deleted in a `finally`, with the demuxer forced to
   the format the magic bytes named and only the `file` protocol allowed (this stops a
   crafted file making ffmpeg open other files or URLs), 10-second timeout. Exactly one
   audio stream, no video, codec Opus, AAC, PCM, MP3 or Vorbis.
4. **422, length by decoding:** ffmpeg decodes at most 181 seconds to nowhere and reports how
   far it got. Over 180 is refused. This ignores header timestamps a file can fake, and
   covers Chrome's WebM and Safari's MP4, which may carry no duration in their headers.
5. **Queue caps:** a user with 3 memos waiting gets 429; when 10 minutes of audio already
   wait in the org, 503 "busy, try again shortly". These replace a per-minute rate, which
   would still let one person queue an hour of CPU.

Who may upload: whoever may write a note (`may_note`, `firstcall/core/outcomes.py:41`);
anyone else gets 404. An action needs `may_log_contact`, as `add_note` checks
(`firstcall/board/notes.py:77-78`).

### The voice note and the contact

In one transaction, under the lead's row lock, as `add_note` does (`notes.py:61-95`):
- a note with `source = voice`, empty text, read state `transcribing`, timed now;
- the audio row (`voice_audio`: note, bytes, format, seconds);
- a `transcribe` job on the note (jobs already take a note, `firstcall/db.py:269`);
- `note.added` with `source` in its payload;
- the contact, if an action was picked, logged now through `log_contact` (`notes.py:98`).
  The person's pick stands even if transcription later fails, exactly as a typed action
  does. No model, no transcript, needed.

### The transcriber

- **A second worker process** (decision 2): `claim` gains a kinds filter; the existing
  worker skips `transcribe`; `firstcall worker --kind transcribe` takes only those. Today
  `run` sends any job with a note to the reader (`firstcall/pipeline/jobs.py:123`), so it
  dispatches by kind instead.
- **Three steps, not one transaction** (`run` holds one for the whole job today,
  `jobs.py:122`):
  1. Read the audio bytes; commit.
  2. Decode and transcribe, with no transaction open. ffmpeg decodes from a pipe (forced
     demuxer, `pipe` protocol only, at most 181 seconds, so memory stays near 12 MB)
     into samples faster-whisper takes directly.
  3. Lock the lead, then the note (delete lead's order). Write only if the job is still
     `running` on this same attempt and the note is still `transcribing`; otherwise drop the
     result. Then fill the text, set it `pending`, queue `read_note`, delete the audio, mark
     the job done. PR3's reader takes over (`firstcall/notes/reader.py:98-138`).
- **Lease and health:** the lease thread (`jobs.py:83-108`) catches and logs its own errors
  and keeps renewing, and also writes the heartbeat, so a long memo never looks dead
  (`worker.py:30,110-113` beats only between jobs today).
- **Failures:**
  - Won't decode, no speech, over 180 seconds once decoded, or a transcript over
    5,000 characters (`NOTE_MAX`, `notes.py:33`): fail at once, no retry.
  - Anything else retries, up to 5 attempts. Out of attempts, `_fail` (`jobs.py:41-55`)
    marks the note `failed`, reason `not_transcribed`, and deletes the audio. The reaper
    reaches the same `_fail` (`jobs.py:180-182`).
  - The page says "Couldn't transcribe this memo. Record it again." A logged contact stays.
- **Bounded waiting:** the scheduler's reaper (`worker.py:65`) also fails any note still
  `transcribing` after 30 minutes and deletes its audio. The scheduler, not the
  transcriber, runs it, so audio cannot sit for days while no transcriber runs.
- **Delete lead** already deletes a lead's note jobs (`firstcall/board/delete.py:76`); it
  also deletes `voice_audio`, which the delete test must classify
  (`tests/test_delete_lead.py:43-86`).
- **Modes:** `local` (faster-whisper, the default) and `replay` (transcript looked up by the
  audio's SHA-256 in a committed file; a miss raises). faster-whisper is imported only in
  `local`.
- **"Deleted"** means the row is gone at commit. The bytes stay in Postgres's write-ahead
  log and backups until those turn over. The docs say so.

### Whisper

- **Candidates,** fixed in ADR-005 before any result: `tiny.en`, `base.en`, `small.en`.
- **Fixed settings,** also in ADR-005: `int8` on CPU, 2 threads, language `en`, beam 5,
  the built-in voice detector on (it skips silence, where Whisper invents text), no
  conditioning on earlier text (it stops repeat loops).
- **Measured today** on this Mac (M5, 2 threads, int8, two hand-written clips, not seed 11):

  | Size | Compute s per audio minute, clean / degraded | Peak memory |
  |---|---|---|
  | tiny.en | 2.4 / 1.8 | 393 MB |
  | base.en | 2.8 / 3.6 | 520 MB |
  | small.en | 8.3 / 11.9 | 957 MB |

  Atlas will be slower; by how much is unverified until decision 5's timing.
- **The model** downloads on first use from Hugging Face at a pinned revision into a named
  volume, `whisper-models`, and stays loaded. It is never in an image.
- **Images:** ffmpeg goes in the app image (the api needs it). faster-whisper goes in a
  `voice` dependency group, installed by a `voice` Dockerfile stage. The group is in uv's
  default groups, so `uv sync --locked` installs it locally and in CI and pyright resolves
  the import; the app stage leaves it out.
- **Compose:** `transcriber` builds the `voice` stage, takes 2 CPUs and 1.5 GB, and has
  profile `voice`. So `verify.sh`'s `docker compose build` and `up -d --wait`
  (`verify.sh:32-33`) never build it, start it or download a model.

### The seed-11 audio (gold, before any transcription code)

- **No real person is recorded.** Piper renders all 150 seed-11 scripts. Only voices whose
  model card allows reuse of the output (CC0, CC BY, CC BY-SA, MIT, public domain) are
  used, listed in `LICENSES.md`.
- **Voices:** at least 6, including UK English (Piper's `en_GB` voices; `alba` tried today)
  and other accents where licensed (unverified which). Each script keeps one voice for its
  clean and degraded clip. The report lists the voices (F38).
- **Spoken text:** a fixed rule writes each figure out in words before Piper reads it
  ("four hundred twenty-five thousand dollars"), stored in `spoken.jsonl`. Gold labels do
  not change.
- **Degraded:** speed ±10% (Piper's own rate), generated noise at 10 dB signal-to-noise, a
  300 to 3,400 Hz filter, an Opus round trip at 12 kbps. All seeded.
- **Stored** as `evals/gold/voice/seed-11/{clean,degraded}/<script>.mp3`, 16 kHz mono,
  24 kbps, with `clips.jsonl` and its own `MANIFEST.json`, as the note gold has
  (`builds/pr3/log.md:5-8`). A test keeps it under 15 MB.
- **Order:** the audio, manifest, spoken text, voice scorer and ADR-005's candidates and
  settings precede `firstcall/voice/transcribe.py` in git history, checked by the note
  gold's helper (`tests/test_note_corpus.py:185-208`).

### The voice eval (F38)

- `firstcall/evaluation/voice_score.py`, frozen with the gold: WER (lowercase, no
  punctuation, numbers read both sides by the note scorer's reader), and the hand-off of
  transcripts to the frozen note scorer.
- **Names heard, not spelled** (decision 4): 35 of seed 11's 150 scripts name someone whose
  name has another spelling in the scorer's name list (counted: Stephen and Steven, Brian
  and Bryan, Meghan and Megan, Theresa and Teresa, Ashlee and Ashley, Philip and Phillip,
  and more). The scorer flags a listed name not in the script
  (`firstcall/evaluation/note_score.py:230-232,322-336`), and the floor allows 1 of 150.
  No transcriber can hear a spelling, so every size would miss. The fix: for voice only, a
  name counts as supported when it is a spelling of a name in the script, from a fixed
  table committed with the gold. The strict count is shown on a second, labelled line.
  Numbers keep the strict rule: a misheard figure is an error the salesperson would see.
- **A run,** `firstcall eval-voice --size S --condition C`: transcribe 150 clips locally
  (free) into `evals/voice/transcripts/<size>-<condition>.jsonl` with compute seconds; read
  each through the reader with the frozen prompt, as `evaluate_notes` does
  (`firstcall/evaluation/note_eval.py:126-170`); score against the script and its labels.
- **Report:** per size and condition, every notes floor with intervals, seen and unseen
  phrasing, WER, action precision and recall (reported, no floor), compute seconds per
  minute, the voices, the call log.
- **Pick rule:** the smallest size whose degraded run meets every notes floor and runs
  within 60 compute seconds per audio minute on Atlas (ADR-005's own bound). If none
  does, the largest size within the speed bound ships and each miss goes to
  `docs/failures/`. Point estimates decide, as in every earlier eval
  (`builds/pr3/plan.md:143-144`).
- **Synthetic check:** record mode sends only generator output to a model
  (`firstcall/model/synthetic.py:26-36`). It gains the committed transcripts, accepting a
  line only when its audio hash is in the voice manifest.
- **Replay:** `scripts/check_replay_report.py` learns a `voice` kind
  (`check_replay_report.py:28-56`). It re-scores committed transcripts with recorded reads,
  no Whisper, carrying compute seconds over as it carries the notes record pass
  (`check_replay_report.py:48-50`).

## CI and test time

| Check | Where | Why it stays fast |
|---|---|---|
| Limits, storage, deletion, job, no-key, access tests | PR test shards | A fake transcriber; fixtures under 400 KB |
| Tests calling real ffprobe and ffmpeg | PR test shards | ffmpeg installed in CI's setup step; `verify.sh` fails with a plain message if it is missing, never skips |
| Voice scorer and WER | PR test shards | Pure Python |
| Voice replay report | PR eval job | No Whisper; transcripts are committed |
| F40 page test | Browser job | The transcriber runs from the app image in `replay` mode; no voice image |
| Real Whisper | Never in CI | One logged run in the built voice image before 4a merges; the eval runs locally once |

Expected: under 20 seconds more on a PR run, plus ffmpeg's install (all unverified until
the CI PR measures them).

## Phases

Builders commit at each phase end, build folder included. Tests first. The checks for what
a phase touched run at each phase end; the full `./verify.sh` at the close.

### PR4a

**Phase 1: voice gold and its frozen scorer.**

| Check | Maps to |
|---|---|
| 300 clips; each script one voice for both clips; 6 or more voices, one UK English; licenses listed | F38 |
| Every gold budget figure reads back from `spoken.jsonl` to the same value | F38 |
| Voice manifest matches; existing gold unchanged; folder under 15 MB | F38 |
| WER cases; the name-spelling table, applied to voice only, with the strict line beside it | F38 |
| ADR-005 lists candidates, settings and the speed bound, with no result | F38 |
| Gold before code, from git history | F38 |

**Phase 2: upload, limits, the job and deletion.**

Migration 0012: `voice_audio`, `notes.source`, read state `transcribing`.

| Check | Maps to |
|---|---|
| Each format fixture accepted; renamed text, WebM with video, wrong declared type, undecodable file: 415; 5 MB + 1 byte: 413 unread; 3:05 clip: 422; a file whose header claims 10 seconds but decodes past 180: 422; nothing stored for a refusal | F36 |
| 4th waiting memo for one user: 429; org over 10 waiting minutes: 503 | F36 |
| Success: text filled, `pending`, `read_note` queued, audio row gone | F37 |
| Fail-at-once cases, exhausted retries, a reaped job out of attempts, and the 30-minute expiry: note `failed`, no audio row | F37 |
| A finish from an old attempt, or after a delete, writes nothing; renewal survives a database error; the heartbeat moves during a long fake transcription (fake clock) | F37 |
| Picked action: the contact is logged at upload and stays when transcription fails | F39 |
| No key: the transcript is saved and the note says "Needs a model key"; nothing returns 500 | F39 |
| The main worker never claims `transcribe`; the transcriber claims only `transcribe` | F37 |
| A new access matrix covers the upload route for every role, own and other team; refusals 404 | F36 |
| Delete test classifies `voice_audio`; `note.added` allowlist gains `source` (test-only commit, `tests/test_note_access.py:171`) | F37, F39 |

**Phase 3: the page, the image and compose.**
- Record, Stop, timer, file picker, action select, "Voice" badge, the states.
- ffmpeg, the `voice` stage, the `transcriber` service and volume.
- **The fixture is a real browser recording:** Chromium's fake microphone plays a Piper
  clip of the `unit-page` note (`evals/gold/unit-notes.jsonl:6`), and the WebM it records is
  committed.
- **One real transcription before merge:** the built voice image, `small.en`, that fixture.
  Logged in the build log: transcript, seconds, memory.
- **Playwright on compose (browser job), replay transcription, no key, no reload:** the
  owner picks Called and sends the fixture, sees "Transcribing…" turn into the transcript
  as a Voice note, the contact logged, and "Needs a model key". A `<script>` transcript
  renders as text. F40's first half.

### PR4b

**Phase 4: the runner** (no key): every metric from fake transcripts and answers; an
incomplete run writes no report; record mode refuses a transcript whose audio is not in the
manifest; replay rebuilds the report byte for byte. F38.

**Phase 5: seed-7 dry run.** 30 seed-7 scripts rendered degraded (not committed),
transcribed by all three sizes, read and scored. It shakes out the pipeline, spoken rule
and WER before the run-once seed 11. A fix it needs goes through the gold CHANGELOG, before
any seed-11 transcription. About $0.03.

**Phase 6: Atlas timing (after your go, decision 5).** All three sizes, 20 degraded seed-7
clips each, the voice image limited to 2 CPUs, about 10 minutes, nothing left running.

**Phase 7: seed 11, once per pair (after your go).** All 300 clips through all three sizes
(free), all six transcript sets read (900 calls; PR3 measured $0.000278 a call,
`builds/pr3/log.md:302`, so about $0.25), plus the F40 fixture's read. With phase 5,
**about $0.28; cap $0.40.**

**Phase 8: close.** Default size from the pick rule; ADR-005 closed (disclosing that the
spoken rule was shaped by the hand-written test clips); misses in `docs/failures/`; F40
completed in replay: the suggested call shows, is confirmed, and the contact is logged.

| Check | Maps to |
|---|---|
| A test applies the pick rule to the committed report and gets the shipped size; fake reports cover "smallest passes", "none passes" and "passes but too slow" | F38 |
| One run per (size, condition), 150 reads each, all under the frozen prompt hash | F38 |
| Replay reproduces the voice report | F38 |
| Upload, transcript, suggestion, confirm, contact logged, with no reload | F40 |

## Acceptance

- **F36** Bad format, oversize and overlong files are refused with their codes before
  transcription (phase 2).
- **F37** The audio is gone after transcription, on success, failure, exhausted retries and
  the 30-minute expiry (phase 2).
- **F38** The voice eval on seed-11 audio, clean and degraded, reports WER, note results and
  compute seconds per minute, and lists the voices; the shipped size follows the pick rule;
  misses follow the missed-floor rule (phases 1, 4 to 8).
- **F39** With no key, a memo is transcribed and saved as a note saying "Needs a model
  key", with no 500; a picked contact is logged regardless (phase 2).
- **F40** Playwright: upload a fixture memo, see the transcript and a suggested action,
  confirm it, see the contact logged (phases 3 and 8).

## Other choices (my call, stated so they can be overruled)

- Piper runs outside the lock, since it is GPL now; only its audio is committed.
- Raw-body upload; no new dependency.
- 30-minute expiry, and caps of 3 memos a user and 10 minutes of audio an org.
- Unsupported facts scored against the script, so a misheard number counts.

## Decisions for Cristian

1. **A CI speed-up PR before PR4.** Firstcall CI is one 13.5-minute job. *Recommend:* yes,
   in Docket's shape (step-split `verify.sh`, parallel jobs, 4 test shards, the fault tests
   on main, nightly and manual runs). Voice then adds seconds, not minutes.
2. **A separate transcriber process.** One worker runs every job in turn, so a memo would
   hold new-lead alerts past the 2-second floor. *Recommend:* yes, same code, a second
   process limited to 2 CPUs and 1.5 GB, behind the `voice` compose profile.
3. **Audio waits in Postgres, at most 30 minutes.** It commits with its note and job and is
   deleted in the transaction that finishes it; the scheduler fails anything older.
   *Recommend:* yes. A shared folder needs a volume across two containers and an orphan
   sweep.
4. **Voice names scored by sound, not spelling** (a change to the frozen scorer, voice
   only). Without it, 35 of 150 scripts carry a name Whisper must guess the spelling of,
   and the 1-in-150 unsupported floor fails at every size for a reason that is not hearing.
   *Recommend:* yes, with the strict count on a second line. The alternative is to publish
   it as a known limit and let speed alone pick the size.
5. **Atlas timing and the spend.** *Recommend:* yes to one 10-minute timing run on Atlas
   (it is now part of the pick rule, and v01 says PR4 measures it,
   `builds/v01/plan.md:890-891`), and up to $0.40 of the Firstcall key, about $0.28
   expected.

## The Bounce, answered

| # | Point | Answer |
|---|---|---|
| 1 | CI lanes missing; voice image in every run; skips; pyright | Accepted. CI PR first (decision 1); `voice` compose profile keeps the image and model out of `verify.sh`; ffmpeg installed, missing fails, never skips; voice group in uv's default groups so pyright resolves. "CI and test time", "Images", "Compose" |
| 2 | Transcriber unhealthy mid-memo | Accepted. The lease thread writes the heartbeat. "The transcriber" |
| 3 | One long transaction | Accepted. Three steps, no transaction during transcription |
| 4 | Late finish; renewal dies silently | Accepted. Attempt and state checked on finish; renewal catches errors |
| 5 | Audio unbounded without a transcriber | Accepted. 30-minute expiry in the scheduler's reaper; decision 3 |
| 6 | Rate limit lets an hour of CPU queue | Accepted. Per-minute rate replaced by caps of 3 per user and 10 minutes per org |
| 7 | Forged timestamps | Accepted. Length is measured by decoding, capped at 181 seconds, in the api and the transcriber |
| 8 | ffprobe on the event loop | Accepted. Threadpool, as intake (`api.py:101,143`) |
| 9 | No contact without the model | Accepted. Action select kept; the contact logs at upload and survives a failed transcription |
| 10 | Lead page does not poll | Partly wrong: it polls while a note is read (`detail.ts:170-215`). Accepted for memos: `transcribing` joins its waiting states |
| 11 | NOTE_MAX, Safari MP4, WAL | Accepted. Over 5,000 characters fails at once; decode-based length covers MP4; WAL and backups stated |
| 12 | Name spelling sinks the floor | Confirmed by count: 35 of 150 scripts. Decision 4. Numbers stay strict |
| 13 | Seed 11 peeked at | Rejected: the test clips were hand-written from `unit-page` (`evals/gold/unit-notes.jsonl:6`); its "Dana" appears in no seed-11 script (`grep -c Dana` = 0). ADR-005 still discloses that they shaped the spoken rule |
| 14 | No dry run | Accepted. Phase 5, seed 7 |
| 15 | No margin, no speed gate | Speed gate accepted: 60 s per audio minute on Atlas, in the pick rule. Margin rejected: every eval decides on point estimates (`builds/pr3/plan.md:143-144`), and v01 accepted one held-out seed for voice (`builds/v01/plan.md:329-330`) |
| 16 | No real Whisper in 4a's image | Accepted. One logged `small.en` run in the built image before merge |
| 17 | Fixtures not from a browser | Accepted for the fixture that matters: F40's WebM is recorded by Chromium. Format-limit fixtures stay ffmpeg-made, since they test the limits, not the recorder |
| 18 | F40 passes with a reload | Accepted. No reload in F40 |
| 19 | F37 with audio at rest | Same as 5 |
| 20 | Circular pick-rule test | Accepted. Fake reports cover each branch, the speed gate included |
| — | Over-built | All cut: no stop-at-first-pass, no per-voice or per-style WER, no tmpfs, no fetch-model checksum, no nightly smoke, no process tests |
