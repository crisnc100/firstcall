# Recording model answers again

Every model answer in a committed report comes from a live call to Claude Haiku 5.5 on the
Anthropic API. The answers are saved in `evals/recordings/claude-haiku-5-5.jsonl`, and the
reports in `evals/reports/` are made from them in replay mode. CI reruns each report in
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
seed costs about $0.12; seeds 7 and 11 measured $0.1147 and $0.1155 by the ledger. The measured totals are in the reports' "Calls and cost" tables and
in the build log.

## Replaying

```bash
uv run firstcall eval --seed 11 --mode replay --date YYYY-MM-DD
uv run python -m scripts.check_replay_report
```

Replay needs no key and makes no call.
