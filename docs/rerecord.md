# Recording model answers again

Every model answer in a committed report comes from a live call to Claude Haiku 5.5 on the
Anthropic API. Message answers are saved in `evals/recordings/claude-haiku-5-5.jsonl` and
note answers in `evals/recordings/notes-claude-haiku-5-5.jsonl`. The reports in
`evals/reports/` are made from them in replay mode. CI reruns each report in
replay mode with no key and requires the same bytes (`scripts/check_replay_report.py`).

## When to record again

A recorded answer is keyed by the SHA-256 of the model id, the system prompt, the user
message and the answer schema. Changing any of them makes the old answers miss:

- the system prompt or the user message template (`firstcall/model/prompts.py`);
- the answer schema (`firstcall/core/model_read.py`), including the org's item or team
  lists in `config/demo.yaml`, which the schema and the user message carry;
- how an email is shown to the model (`intake_text` in `firstcall/pipeline/model_read.py`),
  or the rules read's body text it includes;
- a corpus message or clean twin (a gold change, logged in `evals/gold/CHANGELOG.md`);
- the model.

A miss raises `ReplayMissError` and fails verify. That is on purpose: a replayed answer to a
different question would make the report wrong without anyone noticing.

Seed 13 is the confirm run. It is recorded once, after `config/field_methods.yaml` is
committed, and its prompts are not tuned against it. Recording it again needs a reason
written in the build log.

## How to record

The key lives in `.env` as `FIRSTCALL_MODEL_KEY`. `.env` is gitignored, and the settings never
read it themselves, so load it into the shell for the one command:

```bash
set -a; . ./.env; set +a
uv run firstcall eval --seed 7  --mode record --budget 2 --date YYYY-MM-DD
uv run firstcall eval --seed 11 --mode record --budget 2 --date YYYY-MM-DD
```

`--budget` is the most that one run may spend, at most $10. Record mode sends every message
and clean twin to the model once, model-only, and saves each answer; a request already
recorded is answered from its recording and costs nothing. A message whose calls failed is
tried again, up to three rounds. The report is then written from the recordings.

After seed 11, write `config/field_methods.yaml` from the report's per-field choice and commit
it. Only then can seed 13 run:

```bash
uv run firstcall eval --seed 13 --mode record --budget 2 --date YYYY-MM-DD
```

Delete the old report files for a seed when you record it again, so CI checks only the new
ones, and commit the recordings with the reports.

## What it costs

Each call is about 1,800 prompt tokens (the schema is counted) and 80 completion tokens, about
$0.00022 at $0.10 and $0.50 per million. A seed is 500 messages plus its clean twins, so one
seed costs about $0.12. By the budget ledger, seeds 7, 11 and 13 measured $0.1147, $0.1155
and $0.1155. The reports' "Calls and cost" tables give each run's calls, tokens and spend from
the call log.

## Recording the note eval

The note reader has its own eval, `eval-notes` (ADR-007). Its answers go in the notes file
above. The key is built the same way, from the model, the system prompt, the user message (which
carries the note's date and the team's time zone) and the schema, so changing the note prompt
or the note schema makes every note answer miss. The note prompt is frozen after seed 7; seed 11
is recorded once against it.

```bash
set -a; . ./.env; set +a
uv run firstcall eval-notes --seed 7  --mode record --budget 1 --date YYYY-MM-DD
uv run firstcall eval-notes --seed 11 --mode record --budget 1 --date YYYY-MM-DD
```

`--budget` defaults to $0.10 and is at most $10. Record mode only sends text from the
synthetic corpus: the note scripts and the five fixed F31 notes. It records every answer,
then writes the report from the recordings in replay mode. Before the replay starts it writes
`<date>-notes-seed-<N>-record-pass.json`, the calls, failed attempts and ledger spend of the
paid pass, so the cost is kept even if the replay fails. If some scripts got no answer, the
run stops with the record pass's numbers and writes no report; run it again to fill the gaps,
since a recorded answer costs nothing the second time.

The report's `structured_output` is true only when no answer used the JSON-in-the-prompt
fallback. The recordings say how each answer was made, so a resumed run counts earlier
answers too.

`uv run firstcall eval-notes --unit` reads the five fixed F31 notes and prints the contact
kinds suggested for each, which should be none.

## Recording the voice eval

The voice eval (ADR-005) runs each Whisper size on seed 11's clean and degraded memos. A run
is one size and one condition. It first transcribes every clip on this machine, which costs
nothing but needs faster-whisper (`uv sync --group voice`, on Linux or on macOS 14 or later
with Apple Silicon); then the note reader reads each transcript with the frozen note prompt,
and its answers go in the notes file above.

```bash
uv sync --group voice
set -a; . ./.env; set +a
uv run firstcall eval-voice --size small.en --condition degraded --mode record --budget 0.10 \
  --models /some/scratch/folder
```

Transcripts and the seconds each clip took go to
`evals/voice/transcripts/<size>-<condition>.jsonl`, and the run's machine, Whisper revision and
record passes to `evals/voice/runs/<size>-<condition>.json`. Each pair is transcribed once:
the command never transcribes over those files, and a clip that fails before Whisper hears it
writes nothing. A record pass is written to the run file as it goes, so one that runs out of
budget, hits an error or is stopped with Ctrl-C still shows what it spent. A hard kill (SIGTERM
or SIGKILL) skips the final write made on a stop, so the read in flight may not be counted. Run the same command again to fill the gaps:
the new pass pays only for transcripts with no answer yet, and the report adds up every pass,
telling answers paid for from answers reused from earlier recordings. That resumes the one
pass; it never transcribes again or replaces an answer, since a recorded answer is reused, not
asked for again. Before any read, each transcript must be of its own clip's audio, as the
clips folder's manifest hashes it (`evals/gold/voice/MANIFEST.json` for the voice gold), and
record mode sends only that pair's transcripts.
`FIRSTCALL_MODEL_OFF` stops every record run that reads before it starts; `--transcribe-only`
makes no model call and is not stopped.

`--transcribe-only` stops after transcribing, with no reads and no key; that is how the
voice image times each size on the beta host. Clips rendered anywhere else take `--clips`
and `--out` (and `--seed` when it is not 11), and never write into `evals/voice`.

The voice page test (`tests/fault/test_pages_voice_suggestion.py`) replays the shipped size's
clean transcript of `note-11-141` and its recorded read, so a new recording of the voice eval
must keep both.

Once all six pairs and `evals/voice/atlas-timing.json` are in, write the report and its pick:

```bash
uv run firstcall eval-voice-report --date YYYY-MM-DD
```

## Replaying

```bash
uv run firstcall eval --seed 11 --mode replay --date YYYY-MM-DD
uv run python -m scripts.check_replay_report
uv run firstcall eval-notes --seed 11 --mode replay --date YYYY-MM-DD
```

`check_replay_report` reruns the notes reports too. The record pass is paid for, so a replay
cannot reproduce it; the check copies it from the committed report. It reruns the voice
report from the committed transcripts and run files, with no Whisper.

Replay needs no key and makes no call.
