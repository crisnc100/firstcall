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
