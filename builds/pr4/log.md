# PR4 build log

# PR4a (branch voice)

## Phase 1: voice gold and its frozen scorer

- **Where the gold lives.** `evals/gold/voice/`, beside the note gold: `seed-11/clean/` and
  `seed-11/degraded/` (150 MP3s each, 16 kHz mono, 24 kbps), `seed-11/clips.jsonl` (voice,
  speed, noise seed and length of each clip), `seed-11/spoken.jsonl` (what each voice read),
  `LICENSES.md`, `name-spellings.json` and its own `MANIFEST.json`, which hashes every other
  file in the folder. `firstcall corpus voice --piper CMD --models DIR` writes it
  (`firstcall/corpus/voice.py`). The message and note manifests and every file they list are
  unchanged.
- **Voices.** Ten, 15 scripts each, one voice per script for both clips: five US English
  (`joe`, `kathleen`, `kristin`, `mike`, `ljspeech`), two UK English (`cori`, `alba`) and
  three VCTK speakers: p245 Irish (Dublin), p326 Australian (Sydney), p237 Scottish (Fife).
  Every model card's data is CC0, CC BY 4.0 or public domain, and so is the model each was
  finetuned from. Left out: non-commercial cards (`semaine`, `hfc_*`, `ryan`, `l2arctic`),
  cards that only link a licence (`alan`, `amy`, `kusal`, `jenny_dioco`, `arctic`), and
  `lessac` with the voices finetuned from it (`northern_english_male`, `libritts_r`), since
  lessac's card links its own Blizzard 2013 licence. The VCTK accents come from a Hugging
  Face listing of VCTK's speaker information; the corpus's own `speaker-info.txt` is inside
  its 10 GB archive and was not downloaded. Models from `rhasspy/piper-voices` at e9c8a394,
  downloaded to a scratch folder and deleted after rendering.
- **Spoken text.** Each figure becomes its full value in words, plus "dollars" where the
  script had "$": "$1.2 million" is "one million two hundred thousand dollars", and
  "450 grand" is "four hundred fifty thousand". The note scorer's reader cannot read "one
  point two million" or a worded "grand", and the plan's check needs every gold figure to
  read back. So the audio carries no number style; ADR-005 says so. Seed 7 passes the same
  read-back, for phase 5. "HOA" needed no rule: espeak spells it out.
- **Degraded.** Piper's own rate at 0.9 or 1.1 (75 each, seeded), white noise at 10 dB
  below the clip's level (seeded per clip), a 300 to 3,400 Hz high-pass and low-pass, then
  an Opus round trip at 12 kbps, then MP3.
- **Names by sound.** `name-spellings.json` holds 45 groups of spellings that sound the
  same, each with a name used in a seed-7 or seed-11 script and only names in the scorer's
  list. Candidates came from three phonetic codes over the scorer's list (jellyfish, a
  one-off tool outside the lock), then were cut by hand to true homophones: Carolyn and
  Caroline, Laura and Laurie, Dennis and Denise, Jaime and Jamie are out. By this table 42 of
  seed 11's 150 scripts hold such a name (the plan counted about 35 with a looser list).
- **Voice scorer** (`firstcall/evaluation/voice_score.py`). WER reuses the note scorer's
  number reader, so the spoken text has WER 0 against its script (a test). The note scorer
  is untouched: voice filters its unsupported findings, keeps the strict count as
  `unsupported_strict`, relabels the floor row "names by sound" and reports the strict count
  as its own line.
- **ADR-005** gains the candidates, settings, speed bound, pick rule, how voice is scored and
  the disclosure of the two hand-written clips. Its prediction is unchanged and its result
  pending. A test reads ADR-005 as it stood at the commit that first added the voice
  manifest.
- **Piper on this Mac.** piper-tts 1.8.0's macOS wheel builds its espeak-ng data path into a
  160-character buffer; under the scratch folder the path is longer, so espeak fell back to
  the CI runner's build path and failed. `ESPEAK_DATA_PATH` pointing at a short symlink to
  the package fixed it. Piper ran from a throwaway venv in the scratch folder; nothing GPL is
  in `pyproject.toml` or `uv.lock`.

## Phase 2: upload, limits, the job and deletion

- **Upload.** `POST /api/leads/{id}/voice?action=…` (`firstcall/board/routes.py`), raw body.
  Order: who may record and the action (404, 422, without reading the body), then
  `Content-Length` over 5 MB (413, unread), the capped read (413), then
  `firstcall/voice/audio.py`'s `check` in the threadpool: magic bytes and the declared type
  (415), ffprobe on a temporary file with the demuxer forced and only `file` allowed (415),
  one audio stream and an allowed codec (415), then ffmpeg decoding at most 181 seconds from
  the same file (422 over 180). Then the store, in the threadpool, under the lead's lock and an org lock
  for the caps (429, 503).
- **Migration 0012.** `notes.source` (typed or voice), read state `transcribing`, and
  `voice_audio` (org, lead, note, format, seconds, bytes). Downgrade fails waiting memos as
  `not_transcribed` and ends their jobs, so the old worker never takes one as a note read.
  Checked once by hand (upgrade, a memo and a typed note, downgrade to 0011, upgrade again);
  no committed test, as for earlier migrations.
- **The transcriber** (`firstcall/voice/job.py`, `firstcall worker --kind transcribe`).
  Three steps as the plan says. `retry_or_fail` and `current` (`firstcall/pipeline/jobs.py`)
  are shared with `run`. `transcribe` mode `local` (faster-whisper, pinned revisions) or
  `replay` (transcripts by SHA-256).
- **Expiry** runs as its own scheduler step, `expire_tick`, beside the reaper, so the
  reaper's `tick` result the tests pin is unchanged. `firstcall tick` runs it too.
- **Fixtures** (`tests/fixtures/voice/`, 180 KB, `make.sh` remakes them with ffmpeg): one
  second of tone in each format, an ADPCM WAV (codec refused), a WebM with video, 3:05 of
  near silence, and the same with its header's duration patched to 10 seconds.

Differences from the plan, and choices it left open:
- ffmpeg decodes from the temporary file ffprobe reads, not from a pipe as the plan said.
  An MP4 with its index (moov) after the audio, as Safari and ffmpeg write them, failed from
  a pipe once past ffmpeg's 32 KB read buffer: a 60-second one was refused 415, the same
  audio with its index first accepted (checkpoint review f1, reproduced). The transcriber
  decodes the same way. `moov-last.m4a` (30 s, 63 KB) and a 2:58 copy remuxed in the test
  hold it.
- The who-may check runs before the 413, so a refusal never depends on the body.
- The org cap refuses once 600 seconds or more already wait ("already wait"); the person cap
  refuses the fourth memo.
- A picked action on a memo logs its contact with source `typed`, as a typed note's does:
  the source says a person picked it, not how the note was made.
- `huggingface-hub<2` in the voice group. Hub 2 pulls in `httpx2`, which Starlette's
  TestClient then prefers over `httpx`: every API test's client changed and pyright failed
  on 11 test call sites.
- `retry_or_fail` checks the attempt for message and note jobs too, so an old attempt's
  error no longer puts back a job the reaper gave to another worker. The main worker's lease
  thread also beats.
- The heartbeat and renewal tests wait in real time (under 2 seconds): the lease thread waits
  on a real timer, not the fake clock.
- CI installs ffmpeg with apt in the tests, compose and slow jobs (a `setup` input). Its cost
  is unmeasured until CI runs; `verify.sh --step tests` stops with a plain message without
  it.

## Phase 3: the page, the image and compose

- **The page** (`web/src/pages/work.ts`). Under the note form: Record a memo, Stop and send,
  a timer, and Send a recording (a file picker), all using the form's "This note records"
  pick. The recorder takes the first of WebM, MP4 or Ogg the browser can make, mono, echo
  cancelling and noise suppression on, 32 kbps, and stops itself at 2:59.5, since the
  server refuses anything that decodes past 3:00. A memo note carries a Voice badge and
  "Transcribing…"; a failed one says "Couldn't transcribe this memo. Record it again."
  `api.ts` sends a Blob as the raw body. The lead page polls while a note is transcribing,
  and does not redraw while a memo records (`detail.ts`, `editing`). Another action's
  redraw mid-memo (saving a typed note, say) drops the memo unsent and lets go of the
  microphone.
- **Images.** ffmpeg is in the app image (Debian's 5.1, 398 MB of the image's 1.16 GB;
  the apt step took 14 s here). A `voice` stage adds faster-whisper (2.02 GB). The app
  stage stays last, so a plain build makes the app image.
- **Compose.** `transcriber` builds the voice stage, has profile `voice`, 2 CPUs, 1.5 GB and
  the `whisper-models` volume. `docker compose config --services` without the profile does
  not list it.
- **The fixture** (`tests/fixtures/voice/unit-page.webm`, 37 KB, 10.5 s, Opus, encoder
  Chrome). Piper's `joe` read the unit-page note's spoken text; Chromium's fake microphone
  played that WAV into the lead page's Record button; `scripts/record_memo.py` kept the
  WebM the page sent. Chromium's fake microphone never answers on macOS, so it ran in the
  app image on the compose network:

  ```sh
  docker run --rm --network container:firstcall-voice-api-1 \
    -v "$PWD/scripts:/app/scripts:ro" -v "$SCRATCH:/rec" -e FIRSTCALL_RECORD_PASSWORD \
    firstcall-v01-app sh -c 'apt-get update && apt-get install -y libatomic1 \
      && uv sync --locked --no-group voice && playwright install --with-deps chromium \
      && python -m scripts.record_memo --base http://localhost:8000 --email … --lead … \
           --wav /rec/unit-page-48k.wav --out /rec/unit-page.webm'
  ```
- **One real transcription** in the built voice image (`docker compose --profile voice
  run transcriber`, so 2 CPUs and 1.5 GB), `small.en`, that fixture:
  - Transcript: "Call Dana this morning, budget is around $450,000, once three bedrooms and a
    garage, hoping to move by June." (The script: "Called Dana … Wants three bedrooms …")
  - Download and load: 9.8 s (464 MB). Load from the volume: 1.3 s.
  - Transcription: 1.7 s and 1.8 s for 10.5 s of audio, about 10 s per audio minute.
  - Memory: peak RSS 908 MB; the container's peak 1,289 MB, of which 433 MB is file cache.
  The model volume was deleted after.
- **Page test** (`tests/fault/test_pages_voice.py`, 59 s here). The transcriber runs from
  the app image in replay mode on `tests/fixtures/voice/page-transcripts.jsonl`: small.en's
  transcript for the recording and a script-tag transcript for `ok.webm`. The memo is sent
  before the transcriber starts, so "Transcribing…" is seen, then the transcript, then
  "Needs a model key", with no reload. With "transcribing" left out of the page's polling,
  the test fails. Record and Stop run against a tone from the page's own AudioContext in
  place of the microphone; that memo has no recorded transcript, so with one attempt it
  fails and the page says to record it again. Last, a typed note saved mid-memo drops the
  memo and ends the microphone's tracks.

Differences from the plan, and choices it left open:
- The real recording ran in a Linux container, not on this Mac (above).
- The page test fakes the microphone in the page (an oscillator), not with Chromium's flags,
  so it runs on macOS too. The committed fixture is the real Chromium recording.
- The page test's transcriber gets `FIRSTCALL_MAX_ATTEMPTS=1`, so the unrecorded memo fails
  at once instead of after 30 seconds of backoff.
- `verify.sh`'s secret scan skips `page-transcripts.jsonl`, as it skips the model
  recordings: both are keyed by SHA-256.
- CI time: the compose shards now build an image with ffmpeg (14 s for the apt step here)
  and the page test adds about a minute to one shard. Unmeasured on CI.

## Fix round: the voice group leaves uv's default groups

A change from plan rev 2 ("Images", and Bounce item 1's answer). The plan put the `voice`
group in uv's default groups so that pyright resolves faster-whisper. That broke
`uv sync --locked` on Intel Macs and on macOS 13: onnxruntime, which faster-whisper needs,
has wheels only for `macosx_14_0_arm64` on macOS (1.32.0), and no source. The goal Bounce
item 1 approved was that pyright resolves faster-whisper and PR CI never downloads a model,
and the new setup keeps both:
- `voice` is no longer a default group. Only the Dockerfile's voice stage installs it, as
  before; locally and in CI, `uv sync --locked` installs `dev` alone, which gains `numpy`
  for the transcriber's tests.
- Pyright reads `typings/faster_whisper/__init__.pyi`, a stub of only what
  `firstcall/voice/transcribe.py` calls, written for faster-whisper 1.2.1.
  `firstcall/voice` does not report the missing package source; a missing import still
  fails there.

Checks:
- `MACOSX_DEPLOYMENT_TARGET=13.0 uv sync --locked --dry-run --python-platform …` into an
  empty environment: before, `aarch64-apple-darwin` and `x86_64-apple-darwin` both stop
  on onnxruntime ("only has wheels for … `macosx_14_0_arm64`"). After, both would install
  71 packages, with none of onnxruntime, ctranslate2, faster-whisper or av.
- Pyright: 0 errors, 0 warnings, with the stub in use and nothing of the voice group
  installed. Run locally, `uv sync --locked` removes the voice packages from an
  environment that had them.
- The app image has none of ctranslate2, onnxruntime, faster-whisper, av or numpy; the
  voice image has all of them, faster-whisper 1.2.1.
- In the voice image, every parameter and field the stub names exists in faster-whisper
  1.2.1. A real `small.en` run there, on `unit-page.webm`, gave the same transcript as
  phase 3: 11.0 s with the download and load, 1.9 s for a second run. The model volume
  was deleted after. Such a real run in the voice image is the check against the stub
  drifting from the library.

# PR4b (branch voice-eval, from main d3fe098)

## Phase 4: the runner (no key)

- **A run** is one Whisper size on one condition: `firstcall eval-voice --size S --condition C
  [--mode record|replay]` (`firstcall/evaluation/voice_eval.py`). It transcribes every clip
  first, timing each `transcribe` call (decoding included) with a wall clock, into
  `evals/voice/transcripts/<size>-<condition>.jsonl` (id, audio sha256, note text, the reason
  when there is no speech, compute seconds), and writes `evals/voice/runs/<size>-<condition>.json`
  (seed, size, Whisper revision, the machine's CPU, record passes). Both are written whole or
  not at all, and the command refuses to transcribe a pair that has either: each pair is
  transcribed once. Then every transcript is read as `evaluate_notes` reads a script and
  scored with `voice_score`, WER against the script.
- **The transcriber** is built by `local_transcriber`, which is `from_settings` with local mode
  named, so a broken setup (no ffmpeg, no faster-whisper, a model that will not load) stops
  the command before the first clip, and `FIRSTCALL_TRANSCRIBE_MODE=replay` in the shell
  cannot stand in for Whisper.
- **Record passes** join the pair's run file before the replay pass, so a replay that fails
  keeps what was paid. A pass that leaves transcripts unanswered stops; a rerun pays only for
  the rest, since a recorded answer is free.
- **Synthetic check.** `SyntheticIndex.from_gold` also takes each transcript whose audio
  sha256 is an `.mp3` in the voice manifest. The voice runner builds its index from the
  transcripts folder and clips folder it reads, through a new optional argument to
  `note_eval._gateway`.
- **Report:** `firstcall eval-voice-report --date D` writes `evals/reports/<date>-voice-seed-11`
  in replay mode from the transcripts, the recorded answers, the run files and
  `evals/voice/atlas-timing.json`: the pick, then a row per pair (floors met, WER, not
  transcribed, unreadable, compute seconds per audio minute), then each pair's floors with
  95% intervals, seen and unseen, the by-spelling line, action precision and recall, the
  calls and record passes, and the voices. It is written only when all six pairs are in,
  every transcript answered, and the Atlas timing there.
- **Replay check.** `scripts/check_replay_report.py` reruns `voice` reports. Nothing is
  carried over from the committed report: compute seconds and record passes are committed
  inputs under `evals/voice/`.
- **Pick rule** (`pick`): the smallest size whose degraded floors are all met and whose Atlas
  time is at most 60 s per audio minute; else the largest size within the bound, with its
  misses; else no size.

Choices the plan left open:
- One report for all six pairs, written by its own command, rather than one per pair, so the
  pick reads one file and CI replays one report.
- A transcript with no speech is kept as an empty transcript with its reason and scored as an
  answer that fills nothing, with its own count; the product would fail that memo.
- If no size runs within the speed bound, the rule names none; the plan does not say what
  ships then.
- `evals/voice` takes only the voice gold's runs; other clips need `--out`.

Checks: `tests/test_voice_eval.py`, 23 tests, on six seed-11 scripts' clips copied with their
own manifest and fake transcribers and answers. Every metric from fake transcripts and
answers (`test_every_metric_from_fake_transcripts_and_answers`); an incomplete run writes no
report (`test_a_run_that_cannot_answer_every_transcript_writes_no_report`,
`test_a_report_with_a_pair_missing_is_not_written`,
`test_the_report_command_writes_nothing_while_the_eval_is_incomplete`); record mode refuses
a transcript whose audio is not in the manifest, before any call
(`test_record_mode_refuses_a_transcript_whose_audio_is_not_in_the_manifest`); replay
rebuilds the report byte for byte (`test_replay_rebuilds_the_report_byte_for_byte_and_it_picks`);
the pick rule's branches on fake reports; a missing faster-whisper stops the command with no
file written. voice_eval.py branch coverage 97%.

Found on main: `test_voice_gold_and_its_scorer_were_committed_before_any_transcription_code`
fails since PR4a's squash merge (d3fe098 adds the voice gold and `transcribe.py` together;
main's CI run after the merge is red on it). PR4a's branch tip e6d49b1 has d3fe098's tree, so
a `pr4a-history` tag there and a `SQUASHED` entry in `tests/test_corpus.py` fix it, as
`pr1a-history` did for PR1a; checked locally with the tag, not yet committed or pushed.

## Main's gold-order test (test-only commit 08bb7c8)

`SQUASHED` in `tests/test_corpus.py` gains `"d3fe098": "pr4a-history"`; the tag, pushed by
the orchestrator, points at e6d49b1. No assertion changed.

## Phase 5: seed-7 dry run (2026-11-21)

- **Clips.** 30 degraded seed-7 clips, the first three scripts of each of the ten voices in
  `plan_clips(7)`, rendered into a scratch folder with the gold's own `render_clip` and given
  their own `MANIFEST.json` by `write_voice_manifest`. Piper 1.8.0 ran from a throwaway venv
  with the ten voices at the gold's revision; the venv and models were deleted after. Seed 7's
  audio, transcripts and recorded answers stayed in scratch; nothing of seed 7 is committed.
- **Runs.** `eval-voice --seed 7 --condition degraded --clips … --out … --recordings …`, each
  size transcribed with `--transcribe-only` first, then `--mode record --budget 0.05`. Record
  mode accepted every transcript against the scratch manifest. A replay of small.en printed
  the same floors.

| Size | WER | Floors met | Unsupported, by sound (by spelling) | Compute s per audio minute, this Mac | Ledger $ |
|---|---|---|---|---|---|
| tiny.en | 0.2775 (227 of 818) | 1 of 8 | 5 (6) | 2.44 | 0.008078 |
| base.en | 0.2103 (172 of 818) | 4 of 8 | 8 (9) | 4.30 | 0.008067 |
| small.en | 0.1479 (121 of 818) | 6 of 8 | 4 (4) | 12.83 | 0.007895 |
| **Total** | | | | | **0.024040** |

90 reads, 0 failed, all on structured output; one small.en transcript matched an earlier
one and was answered from its recording.

What it shook out:
- **The pipeline** works end to end: from_settings' startup check, timing, the manifest
  check, reads, scoring, replay.
- **WER** errors are mishearings on degraded audio ("Wants" heard as "Once" in most voices,
  place names, "Lease" as "Lisa"). One spelling difference counts as hearing: Whisper's
  "e-mail" for "email" is two errors (once each for base.en and small.en, 2 of 818 words).
  WER has no floor, so the frozen scorer is unchanged.
- **The spoken rule.** "five hundred five thousand" (505,000) was heard as $500,000
  (small.en) and as "500,000, 5,000" (base.en): spoken without "and", the figure is
  ambiguous. Five seed-11 scripts say "… hundred five thousand". Unchanged: a salesperson
  can say it the same way, and changing it means rendering the seed-11 gold again.
- **Names by sound** did their job: "Sean" for "Shawn" counted only on the strict line.
- **Every unsupported finding is a real mishearing**: a misheard figure (840k as 340k or
  140k), a word heard as a name ("Lease" as "Lisa"), or a name heard as another
  ("Catherine" for "Kathleen"). No size reached the unsupported floor (at most 1) on 30
  scripts.

No gold, scorer or pipeline fix came out of the dry run, so `evals/gold/CHANGELOG.md` is
unchanged.
