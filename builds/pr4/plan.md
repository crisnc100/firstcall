# Firstcall PR4: voice (build plan, rev 1)

Source: `builds/v01/plan.md`, "PR4: voice" (F36 to F40), with the definitions it relies on:
note scripts, voice memos, the notes floors, the missed-floor rule, seed 11, the no-model
fallback, model modes, and the PR6 note that Whisper runs in the worker. PR3's shape and
its build log (`builds/pr3/plan.md`, `builds/pr3/log.md`) are the model for this plan.
Branch `voice`, cut from main at 3107006. Nothing here is built until Cristian approves it.

Terms used below:
- **Voice memo:** a short recording a salesperson makes about a lead. It becomes a note.
- **Transcript:** the text Whisper writes from a memo.
- **Whisper size:** Whisper comes in sizes. Bigger is more accurate, slower and uses more
  memory. `.en` sizes are trained on English only, which suits an English demo.
- **WER (word error rate):** the share of words a transcript gets wrong against what was said.
- **Clean / degraded clip:** a memo rendered by text-to-speech as is, and the same memo made
  harder: noise, a phone-band filter, a lossy codec and changed speed.
- **Replay:** answers saved from a real run and read back, so tests and CI need no model,
  no key and no Whisper. A missing answer raises.
- **PR lane / slow lane:** the CI checks every pull request waits on, and the longer checks
  that run on `main` and nightly.

## Outcome

A salesperson records a memo on a lead's page, or picks an audio file. The browser uploads
it. The server refuses a wrong format (415), a file over 5 MB (413) or a memo over 3 minutes
(422) before any transcription. A separate transcriber process turns the audio into text with
faster-whisper, locally, with no key. The transcript becomes a note marked "voice", and
PR3's note reader reads it like a typed note: tidied note, profile updates, suggested actions
a person confirms. The audio is deleted when transcription ends, whether it worked or not.
With no model key the transcript is still saved, and its read says "Needs a model key".
The voice eval runs seed 11's note scripts as audio, clean and degraded, through each
candidate Whisper size, and ships the smallest size that meets the notes floors on degraded
audio. PR CI never runs Whisper and never downloads a model.

## Out of scope

- The dashboard, including the "share of contacts logged by voice" figure (PR5). PR4 stores
  what it needs: each note's source, typed or voice.
- Hosting, HTTPS and the beta's limits (PR6). PR4 measures Whisper's speed on the beta host
  only if Cristian says yes (decision 4); it deploys nothing.
- CRM events (PR7).
- Tuning the note reader's prompt on voice. The PR3 prompt stays frozen
  (sha256 `93a64f56…`, `builds/pr3/log.md:320`); voice is scored with it as is.
- Choosing an action when recording. The reader suggests the contact from the transcript and
  a person confirms it with one tap. (A typed note keeps its action select.)
- Keeping audio for playback. Only the transcript is kept.
- Speakers other than English, and live streaming transcription.

## Two PRs (recommended; Cristian to confirm)

- **PR4a, the voice path (no key, no spend):** the seed-11 audio and its frozen scorer,
  upload and limits, audio storage and deletion, the transcriber process, the voice note,
  the recording page, and the compose and image changes. Covers F36, F37, F39 and the first
  half of F40.
- **PR4b, the voice eval and the size choice:** the eval runner, the free local
  transcription runs, the paid reads of the transcripts (after your go), the size choice,
  ADR-005 closed, the beta-host timing (after your go), and the rest of F40. Covers F38 and F40.

Why two: PR3 split the same way (`builds/pr3/plan.md:33-51`) and it worked. 4a is about
PR3a's size. Every paid call and every Whisper run that decides something sits in one small
PR. 4a merged alone works: it ships with `small.en`, ADR-005's predicted size, as the
default, and 4b changes the default only if the eval says so. 4b branches from main after
4a merges.

## Plan versus code

Where the code or a quick check disagrees with the master plan. None of these change a
decision silently; each says what this plan does.

1. **300 clips, not 160.** v01 assumed 80 scripts per seed (`builds/v01/plan.md:326-332`).
   PR3 built 150 (`builds/pr3/log.md:10`), so seed 11 rendered clean and degraded is 300
   clips. Seed 11's scripts hold 4,320 words (counted); at the rate measured on two test
   clips (about 3 KB a second at 24 kbps) that is roughly 9 MB, still under v01's 15 MB check.
   *This plan:* 300 clips, 16 kHz mono MP3 at 24 kbps (v01 did not set a bitrate).
2. **piper-tts is GPL-3.0 now.** v01 lists piper-tts in the dev group and calls its license
   unverified (`builds/v01/plan.md:60,68`). Checked today: piper-tts 1.8.0 declares
   `GPL-3.0-or-later`. *This plan:* keep Piper (a v01 decision), but run it as a one-off
   tool (`uv run --with piper-tts==1.8.0`), outside `pyproject.toml` and `uv.lock`, so the
   Apache-2.0 project never lists a GPL package. The committed audio is its output. See
   decision 3.
3. **No ffmpeg in the image yet.** The image is `uv:…-bookworm-slim` with no system
   packages (`Dockerfile:8-20`); v01 says ffmpeg is in the api and worker images
   (`builds/v01/plan.md:61`). *This plan:* add it in 4a.
4. **One worker runs every job, one at a time.** `work()` loops on `run_one`
   (`firstcall/pipeline/worker.py:113`), and `claim` takes the oldest queued job of any kind
   (`firstcall/pipeline/jobs.py:64-70`). A 20-second transcription would hold every new lead
   behind it and break the 2-second alert floor (F28, `builds/v01/plan.md:388`). v01 says
   "Whisper in the worker" (`builds/v01/plan.md:767`). *This plan:* a second worker process
   from the same code that takes only transcription jobs. See decision 1.
5. **faster-whisper breaks with the newest av.** faster-whisper 1.2.1 resolved av 19.0.2,
   and every transcription failed with `TypeError: open() got an unexpected keyword
   argument 'metadata_errors'` (measured). av 18.1.0 works. *This plan:* pin
   `faster-whisper==1.2.1` and `av<19` in the lock.
6. **"Run once" and "pick the smallest size" pull apart.** v01 says the voice confirm run on
   seed 11 runs once per PR (`builds/v01/plan.md:407`) and also that the eval picks the
   smallest size that meets the floors (`builds/v01/plan.md:685-686`), which needs one run per
   size. *This plan:* each (size, clean or degraded) pair runs exactly once, smallest size
   first, and stops at the first size that meets the floors. Every run is committed, and a
   test checks there is one per pair.
7. **F37 says "the test reads the disk".** With audio held in Postgres (decision 2), the
   test reads the audio table and the scratch folder instead. Same promise, different place.
8. **No multipart parser.** FastAPI needs `python-multipart` for form uploads, which is not
   a dependency (`pyproject.toml:8-23`). *This plan:* the browser sends the audio as the raw
   request body with its content type. No new dependency, and the existing capped reader
   (`firstcall/intake/limits.py:66-75`) enforces 5 MB while reading.
9. **CI today is one 13.5-minute job.** `verify.yml` runs all of `verify.sh` in one job, and
   the last main run took 13.5 minutes (run 44320487195, 2026-11-18). The 3-minute CI is not
   on main at 3107006. *This plan:* says which lane each voice check belongs in, so it fits
   whatever lane split lands. If the split is not on main when 4a starts, the builder stops
   and asks.
10. **`data/LICENSES`.** v01 names this file (`builds/v01/plan.md:69`); there is no `data/`
    folder. *This plan:* `evals/gold/voice/LICENSES.md`, beside the audio it covers.

## How it works

### 1. Recording in the browser

- **The recorder:** MediaRecorder, the browser's built-in recording API. The page asks for
  the microphone, mono, with the browser's echo cancelling and noise suppression on.
- **Format, by what the browser supports**, first match wins:
  1. `audio/webm;codecs=opus`: Chrome, Edge, Firefox, and Safari from 18.4 (unverified for
     Safari).
  2. `audio/mp4`: older Safari, which records AAC in MP4.
  3. `audio/ogg;codecs=opus`: Firefox fallback.
- **Size:** recorded at 32 kbps, so 3 minutes is about 720 KB, far under 5 MB.
- **Length:** a visible timer, and the recorder stops itself at 3:00.
- **A file picker** beside the record button takes an audio file. Playwright uses it (F40),
  and it lets someone send a memo recorded elsewhere.
- **Upload:** `POST /api/leads/{id}/voice`, the audio as the raw body, its type in
  `Content-Type`, and the CSRF header like every write. `send()` in `web/src/api.ts:169`
  sends JSON only, so a small raw-body variant is added beside it.
- **The microphone needs a secure page.** Localhost counts; the beta needs HTTPS (PR6).
  Whether an iPhone Home Screen app may use the microphone is unverified until PR6.
- **On the lead page** (`web/src/pages/work.ts:170`, beside the note form, in the On the
  Rail look from PR #7): Record, Stop, the timer, and Send. A sent memo shows
  "Transcribing…", then becomes a note with a "Voice" badge, then the reader's lines
  (`work.ts:129-133`) take over. A failed memo says "Couldn't transcribe this memo. Record
  it again."

### 2. Limits, checked in the api before anything is stored (F36)

In order, each with its code:
1. **Size, 413:** `Content-Length` over 5 MB is refused before reading; the capped reader
   stops at 5 MB for a body that lies about its length.
2. **Magic bytes, 415:** the first bytes must be one of WebM (`1A 45 DF A3`), MP4 (`ftyp` at
   byte 4), WAV (`RIFF` … `WAVE`), MP3 (`ID3`, or an MPEG frame sync) or Ogg (`OggS`). The
   declared content type is not trusted.
3. **ffprobe, 415 or 422:** the body goes to a file in a memory-only scratch folder (tmpfs),
   and ffprobe reads it with the demuxer forced to the format the magic bytes named and only
   the `file` protocol allowed, under a 10-second timeout. Forcing both stops a crafted file
   from making ffmpeg open other files or URLs (a known ffmpeg trick with playlist formats).
   - Not exactly one audio stream, any video stream, or a codec outside Opus, AAC, PCM, MP3
     and Vorbis: 415.
   - Longer than 3 minutes: 422.
   - **A trap:** Chrome's WebM recordings carry no duration in their header (unverified for
     the current Chrome; widely reported). When ffprobe gives no duration, the api reads the
     last packet's time instead. The 5 MB cap bounds that read.
4. The scratch file is deleted in a `finally`, and tmpfs means a crash leaves nothing after
   a restart.

Who may upload: the same people who may write a note (`may_note`,
`firstcall/core/outcomes.py:41`); anyone else gets 404. A per-user limit of 6 uploads a
minute reuses `RateLimiter` (`firstcall/intake/limits.py:31`), so one person cannot queue
an hour of CPU.

### 3. Where audio waits, and when it is deleted (F37)

- **Storage:** Postgres, in its own table `voice_audio` (memo id, bytes). The memo's own
  row, `voice_memos`, holds the lead, author, upload time, format, length, state
  (`queued`, `transcribing`, `done`, `failed`), the failure reason, and the note it became.
- **Why Postgres and not a shared folder** (decision 2):
  - The upload, the memo and its job commit in one transaction, as a message and its job do
    (`builds/v01/plan.md:121`). There is no moment when a file exists without a row, so no
    orphan sweep is needed. v01 removed the audio sweep (`builds/v01/plan.md:937`).
  - Deleting the audio is part of the same transaction that writes the note or marks the
    memo failed. No crash can leave audio behind after a memo is finished.
  - The api and the transcriber need no shared volume, which keeps PR6 simple.
  - 5 MB rows are small for Postgres.
- **The transcriber never writes audio to disk.** It reads the bytes from the row and pipes
  them through ffmpeg (forced demuxer, `pipe` protocol only) into 16 kHz mono samples in
  memory, which faster-whisper takes directly.
- **Deleted on success:** one transaction writes the note, its `read_note` job and its
  `note.added` event, marks the memo `done`, and deletes the `voice_audio` row.
- **Deleted on failure:**
  - A file that will not decode, has no speech, or runs over 3 minutes fails at once, with
    no retry, since retrying cannot help.
  - Any other error retries, like every job, up to 5 attempts. The audio stays until then,
    because a retry needs it.
  - Out of attempts, `_fail` (`firstcall/pipeline/jobs.py:41-55`) marks the memo failed and
    deletes the audio. The reaper reaches the same `_fail` (`jobs.py:180-182`), so a killed
    transcriber is covered.
- **Delete lead** removes the lead's memos and audio. The delete test refuses any table it
  has not classified (`tests/test_delete_lead.py:43-86`), so both new tables must be
  classified. A memo whose lead is deleted mid-transcription ends quietly, as a note job does
  (`jobs.py:128-130`).
- **What "deleted" means:** the row is gone at commit. Postgres frees the space at its next
  vacuum. The docs say so plainly.

### 4. The job and the transcriber process

- **Job:** a third subject on `jobs`. Today a job has a message or a note, exactly one
  (`firstcall/db.py:269`). Migration 0012 adds `voice_memo_id` and makes the check "exactly
  one of three", unique on (memo, kind). Kind: `transcribe`.
- **Two worker processes** (decision 1): `claim` gains a kinds filter. The existing worker
  skips `transcribe`. A new compose service, `transcriber`, runs `firstcall worker --kind
  transcribe` and takes only those. New leads never wait behind a memo.
- **The lease:** the existing 60-second lease is renewed every 20 seconds by a thread
  (`jobs.py:83-108`). Whisper's C++ code releases Python's lock while it works, so renewal
  keeps running (unverified; phase 2 has a test with a slow fake transcriber).
- **The voice note:** `notes` gains `source` (`typed` or `voice`). The note's time is the
  upload time, not the transcription time, so a confirmed contact is dated when the memo was
  made. `note.added` gains `"source"` in its payload; the event payload allowlist test
  (`tests/test_note_access.py:171`) changes in its own commit.
- **Then PR3 takes over.** The `read_note` job reads the transcript exactly like a typed note
  (`firstcall/notes/reader.py:98-138`). With no key it ends `needs_model` and the page says
  "Needs a model key" (F39); the scheduler's reread picks it up later (`reader.py:141`). No
  new no-key code is needed, only the test.
- **Transcription modes**, like the model modes:
  - `local`: faster-whisper. The default.
  - `replay`: the transcript is looked up by the audio's SHA-256 in a committed file. A miss
    raises. Used by tests and the compose page test, so neither runs Whisper.
- **Settings** (`firstcall/settings.py`): transcription mode, Whisper size, threads (2),
  model folder, 5 MB and 180 seconds. All have defaults; none is a secret.

### 5. Whisper in the transcriber

- **Candidates, smallest first**, fixed in ADR-005 before any result: `tiny.en`, `base.en`,
  `small.en`. `medium.en` is not a candidate (decision 5): on 2 shared CPUs it is likely too
  slow (unverified).
- **Fixed settings**, written in ADR-005 before any result, so the eval cannot tune them:
  `int8` weights on CPU, 2 threads, language `en`, beam size 5, the built-in voice detector
  on (it skips silence, where Whisper tends to invent text), and no conditioning on earlier
  text (which stops repeat loops).
- **Measured today** on this Mac (Apple M5, 2 threads, int8, one 17-second clean clip and
  one 12-second degraded clip, faster-whisper 1.2.1). These are not the beta host's figures:

  | Size | Compute seconds per audio minute (clean / degraded) | Peak memory, whole process |
  |---|---|---|
  | tiny.en | 2.4 / 1.8 | 393 MB |
  | base.en | 2.8 / 3.6 | 520 MB |
  | small.en | 8.3 / 11.9 | 957 MB |

  What the two clips showed: `small.en` got both budget figures right on clean and degraded
  audio; `tiny.en` wrote "between 400 and 25 and 400 and $50,000" for a spoken "four hundred
  and twenty-five … four hundred and fifty thousand"; "Called" came back as "Cold" or "Call"
  on smaller sizes, which matters because the reader suggests only past-tense actions. Two
  clips prove nothing; they show what the eval will measure.
- **On the beta host** (Atlas, 4 CPUs, 15 GB, shared with two live demos): the transcriber
  gets a compose limit of 2 CPUs and 1.5 GB. The model loads once, on the first memo or at
  start, and stays in memory. Speed there is unverified; Atlas's CPUs are likely slower than
  an M5, so `small.en` could land near ADR-005's 60-seconds-per-minute prediction. See
  decision 4.
- **Download at first use, not at build time:**
  - `firstcall voice fetch-model` downloads the chosen size from Hugging Face at a pinned
    revision, checks its SHA-256, and stores it in a named volume, `whisper-models`.
  - The transcriber runs it at start if the model is missing, then works offline
    (`HF_HUB_OFFLINE=1`), so a memo never waits on the network.
  - Why not bake it into the image: it adds 75 to 480 MB to every image build, CI included.
    The volume keeps images small and means CI never downloads a model.
- **ffmpeg and ffprobe:** `apt-get install --no-install-recommends ffmpeg` in the image, for
  the api's ffprobe check and the transcriber's decode.
- **faster-whisper as a dependency group:** it goes in a `voice` group, installed only in a
  `voice` stage of the Dockerfile that the transcriber uses. The api and worker images do not
  grow by its libraries.

### 6. The seed-11 audio (gold, committed before any transcription code)

- **No real person's voice is recorded.** Every clip is made by Piper, an offline
  text-to-speech engine, from seed 11's 150 note scripts. Piper's voices are trained on
  public speech datasets; only voices whose model card allows reuse of the output (CC0,
  CC BY, CC BY-SA, MIT or public domain) are used, and each is listed with its license in
  `evals/gold/voice/LICENSES.md`.
- **Voices:** at least 6, including UK English (Piper has `en_GB` voices such as `alba`,
  downloaded and tried today) and other accents where a voice's license allows. Which
  accented voices qualify is unverified until each model card is read in phase 1. Each
  script gets one voice, rotating in a fixed order, and keeps it for both its clean and
  degraded clip, so the two differ only by the degradation. The voices are listed in the
  report (F38).
- **Spoken text:** the scripts are written ("$425k", "450 grand"); a person says them. A
  fixed rule rewrites each figure into words before Piper reads it ("four hundred
  twenty-five thousand dollars"), and `spoken.jsonl` stores the result. The gold labels do
  not change. The rule is fixed and committed before any transcription, because the test
  clip showed it decides whether small sizes hear the number.
- **Degraded**, each step seeded so it rebuilds the same:
  - **Speed:** ±10%, through Piper's own speech-rate setting.
  - **Noise:** generated background noise (a mix of hum and hiss) at 10 dB
    signal-to-noise.
  - **Phone band:** a 300 to 3,400 Hz filter.
  - **Codec:** an Opus round trip at 12 kbps, like a weak connection.
  - All four are applied to every degraded clip. Clean clips get none.
- **Stored** as `evals/gold/voice/seed-11/{clean,degraded}/<script id>.mp3`, 16 kHz mono,
  24 kbps, with `clips.jsonl` (script, condition, voice, speed, noise seed, spoken text,
  SHA-256, length) and its own `MANIFEST.json`, as the note gold has its own
  (`builds/pr3/log.md:5-8`). About 9 MB (estimate). A test keeps the folder under 15 MB.
  Audio is committed so nothing depends on Piper giving the same bytes twice.
- **Fixtures for tests:** one tiny clip per accepted format (WebM, MP4, WAV, MP3, Ogg), a
  3:05 clip, a WebM with video, a renamed text file, all made with ffmpeg from a Piper clip.
  Under 400 KB in all, in `tests/fixtures/audio/`.
- **Order:** the audio, the manifest, the spoken text, the voice scorer and ADR-005's
  candidates and settings are committed before `firstcall/voice/transcribe.py` exists,
  checked from git history by the same helper the note gold uses
  (`tests/test_note_corpus.py:185-208`).

### 7. The voice eval (F38)

- **Frozen in phase 1**, with the gold: `firstcall/evaluation/voice_score.py`, which
  computes WER (lowercase, punctuation removed, numbers on both sides turned into one form
  by the note scorer's number reader) and wires transcripts into the frozen note scorer.
- **The run**, per size and condition, `firstcall eval-voice --size S --condition C`:
  1. **Transcribe** all 150 clips locally, free, and write
     `evals/voice/transcripts/<size>-<condition>.jsonl`: clip, audio SHA-256, text, and
     compute seconds.
  2. **Read** each transcript through the note reader with the frozen prompt, through the
     gateway, as `evaluate_notes` does for scripts (`firstcall/evaluation/note_eval.py:126-170`).
  3. **Score** against the script and its gold labels, not the transcript. A number Whisper
     misheard and the reader copied is an error the salesperson would see, so it counts as
     unsupported. The report also shows unsupported facts against the transcript, to tell a
     hearing error from a reading error.
- **Report** `evals/reports/<date>-voice-seed-11.{json,md}`: per size and condition, every
  notes floor with intervals, seen and unseen phrasing, WER (overall, per voice, per number
  style), action precision and recall (reported, no floor), compute seconds per audio
  minute (median and total, and where measured), the voices, and the model call log.
- **Pick rule** (v01): the smallest size whose degraded run meets every notes floor. If none
  does, `small.en` ships and each miss goes to `docs/failures/` (missed-floor rule; notes
  floors are quality floors and do not block the merge).
- **Synthetic check:** record mode sends only generator output to a model
  (`firstcall/model/synthetic.py:26-36`). It gains the committed transcripts, accepting a
  line only when its audio hash is in the voice manifest.
- **Replay in verify:** `scripts/check_replay_report.py` learns the `voice` kind
  (`check_replay_report.py:28-56`). It re-scores from the committed transcripts and the
  recorded reads, with no Whisper; compute seconds are carried from the transcripts file, as
  the notes check carries its paid record pass (`check_replay_report.py:48-50`).

### 8. CI and test time

The rule: **the PR lane never runs Whisper, never downloads a model, and never builds the
voice image.**

| Check | Lane | How it stays fast |
|---|---|---|
| Limits, storage, deletion, job, no-key and access tests | PR | A fake transcriber; committed fixtures under 400 KB |
| Tests that call the real ffprobe | PR if the runner has ffprobe, otherwise slow | Phase 2 checks the runner; installing ffmpeg there costs time (unverified) |
| Voice scorer and WER | PR | Pure Python |
| Voice replay report | Slow | Re-scores committed transcripts; no Whisper |
| F40 page test | Slow | Compose with `replay` transcription |
| Real Whisper smoke: 3 clips with `tiny.en` | Nightly | The model is cached between runs |
| The eval itself | Never in CI | Run once by the builder, committed |

**Expected cost:** the PR lane gains about 10 to 20 seconds (unverified; a target the
builder measures). The slow lane gains the voice image build, F40 and the replay report,
about 2 to 4 minutes (unverified). This follows Docket, which runs its fault tests on main
and nightly.

## Phases

Builders commit at each phase end, build folder included. Tests first. The checks for what
a phase touched run at each phase end; the full `./verify.sh` runs at the close.

### PR4a

**Phase 1: voice gold, frozen scorer, ADR-005 settings (before any transcription code).**

| Check | Maps to |
|---|---|
| 300 clips, one per script and condition; each script keeps one voice for both; at least 6 voices, one or more UK English; every voice's license in `LICENSES.md` | F38 |
| `spoken.jsonl` covers every script; every gold budget figure reads back from the spoken text to the same value | F38 |
| The voice manifest matches; existing gold hashes unchanged; the folder is under 15 MB | F38 |
| WER tests: known pairs, numbers written two ways, empty text | F38 |
| ADR-005 lists the candidates and the fixed settings, still with no result | F38 |
| Gold before code: the voice gold and scorer precede `firstcall/voice/transcribe.py` in git history | F38 |
| `piper-tts` is not in `pyproject.toml` or `uv.lock` (a test reads both) | Decision 3 |

**Phase 2: upload, limits, storage, the job and deletion (backend).**

Migration 0012: `voice_memos`, `voice_audio`, `notes.source`, `jobs.voice_memo_id` and the
three-way check.

| Check | Maps to |
|---|---|
| Each format fixture is accepted; a renamed text file, a WebM with video, a wrong declared type, and an unreadable file get 415; 5 MB plus one byte gets 413 before the body is read; a 3:05 clip gets 422; a WebM with no header duration is measured from its packets; nothing is stored for a refused upload | F36 |
| Success: the note exists with `source = voice`, its read job is queued, and the audio row is gone | F37 |
| Failure at once (no speech, won't decode), failure after retries, and a reaped job out of attempts: each marks the memo failed and leaves no audio row; the scratch folder is empty after every case | F37 |
| A killed transcriber (failpoint `during_transcribe`, exit 137 and log line asserted) is retried by the reaper and ends with one note | F37 |
| With no key: the transcript is saved, the note says "Needs a model key", nothing returns 500 | F39 |
| The main worker never claims `transcribe`; the transcriber claims only `transcribe` | Decision 1 |
| A slow fake transcriber holds its lease past 60 seconds (fake clock) | F37 |
| The new access matrix covers the upload route for every role, own team and other team; refused pairs get 404; the monitor's lead payload has no memos | F36 |
| The delete test classifies both new tables; a memo in flight when its lead is deleted ends quietly | F37 |
| The `note.added` payload allowlist allows `source` (own commit, test files only) | F39 |

**Phase 3: the page, the image and compose.**
- Record, Stop, the 3:00 timer, Send, the file picker, the memo states and the "Voice" badge.
- The Dockerfile's ffmpeg and `voice` stage; the `transcriber` service with its CPU and
  memory limits and the `whisper-models` volume; tmpfs scratch for api and transcriber;
  `firstcall voice fetch-model`.
- **Playwright on compose, replay transcription, no key:** the owner picks the WebM fixture,
  sees "Transcribing…", then the transcript as a "Voice" note saying "Needs a model key"; a
  `<script>` in a transcript renders as text. A second check records 2 seconds through
  Chromium's fake microphone and sees the upload accepted. Covers the first half of F40.

### PR4b

**Phase 4: the eval runner (no key, no Whisper choice yet).**

| Check | Maps to |
|---|---|
| `eval-voice` writes every metric, interval, split, WER and compute figure from fake transcripts and fake answers | F38 |
| An incomplete run writes no report; a resumed run spends nothing on recorded reads | F38 |
| Record mode accepts a transcript only when its audio hash is in the voice manifest | F38 |
| The replay check rebuilds a voice report byte for byte | F38 |

**Phase 5: free local transcription.** All 300 clips with each candidate on this Mac. Total
compute is about 10 minutes at the measured rates (estimate). The transcripts are committed.
WER is known now, before any spend.

**Phase 6: paid reads (only after your go).**
1. Degraded transcripts, `tiny.en` first, then `base.en`, then `small.en`, stopping at the
   first that meets every floor.
2. Clean transcripts for the chosen size.
3. The F40 fixture's transcript, once.

At most 4 runs of 150 plus 1, about 600 calls. PR3 measured $0.000278 a call
(`builds/pr3/log.md:302`), so **about $0.17**. **Cap $0.40.** Identical transcripts share one
recorded answer, so it may cost less. Anything past the cap means the builder stops and asks.

**Phase 7: the beta-host timing (only after your go; decision 4).**

**Phase 8: close.** The default size set from the result; ADR-005 closed with its result;
misses in `docs/failures/`; F40 completed: the page test confirms the suggested call from
the replayed read and sees the contact logged.

| Check | Maps to |
|---|---|
| The committed report meets the pick rule; a test applies the rule to the report and gets the shipped size | F38 |
| Exactly one run per (size, condition), each with 150 reads under the frozen prompt hash and no other | F38 |
| The replay check reproduces the voice report | F38 |
| Upload the fixture, see the transcript and a suggested action, confirm it, see the contact logged | F40 |

## Acceptance

- **F36** Each bad format, oversize and overlong file is refused with its code before
  transcription (phase 2).
- **F37** The audio is gone after transcription, on success and on failure, including a
  killed transcriber and exhausted retries (phase 2).
- **F38** The voice eval on seed-11 audio, clean and degraded, reports WER, the note
  results and compute seconds per minute, and lists the voices. The shipped size is the
  smallest that meets the notes floors on degraded audio. Misses follow the missed-floor
  rule (phases 1, 4 to 8).
- **F39** With no key, a memo is transcribed and saved as a note that says "Needs a model
  key", with no 500 (phase 2).
- **F40** Playwright: upload a fixture memo, see the transcript and a suggested action,
  confirm it, see the contact logged (phases 3 and 8).

## Other choices (my call, stated so they can be overruled)

- **No action select when recording.** The reader suggests it; one tap confirms. This avoids
  a typed action waiting on a transcription that may fail.
- **A failed memo stays on the page** as a line saying it failed, with no audio, until the
  lead is deleted.
- **Raw-body upload** instead of a form upload (no new dependency).
- **6 uploads a minute per user.** Cheap, and it stops one person filling the queue.
- **Unsupported facts scored against the script,** as above.

## Decisions for Cristian

1. **A separate transcriber process.** One worker runs every job in turn
   (`worker.py:113`), so a memo would delay new-lead alerts past the 2-second floor. v01
   says "Whisper in the worker". *Recommend:* the same worker code in a second process that
   takes only transcription jobs, limited to 2 CPUs and 1.5 GB on the beta host.
2. **Audio waits in Postgres, not in a shared folder.** The audio, its memo and its job
   commit together, and the audio is deleted in the same transaction that finishes the memo,
   so no crash leaves audio behind and no sweep is needed. F37's "reads the disk" becomes
   "reads the audio table and the scratch folder". *Recommend:* yes. The other option, a
   shared folder, needs a volume shared by two containers and an orphan sweep, which v01
   removed.
3. **Piper is GPL-3.0 now.** *Recommend:* keep Piper (a v01 choice), run it only as a
   one-off tool outside the project's lock, and commit its audio. Nothing GPL ships in the
   image or the lock. The alternative, Kokoro (Apache-2.0 model), means changing a fixed
   stack choice for no gain in proof.
4. **Timing Whisper on Atlas.** v01 says the size's speed on Atlas is measured in PR4
   (`builds/v01/plan.md:890-891`), and the rules say Atlas is used only when you ask.
   *Recommend:* yes, one bounded run: the voice image, 2 CPUs, the chosen size on 20
   degraded clips, about 5 minutes, removed afterwards, nothing left running. The figure goes
   in ADR-005 and the report.
5. **Candidates and spend.** *Recommend:* `tiny.en`, `base.en` and `small.en` only, and up
   to $0.40 of the Firstcall key (about $0.17 expected) for the paid reads, after your go in
   phase 6. If `small.en` misses the floors, it still ships, the misses are published, and
   `medium.en` becomes a v0.2 item rather than a heavier load on the shared host.
