# ADR-002: Model candidates and the pick

- **Status:** Candidate list and prediction committed 2026-11-11, before any model result.
- **Result:** recorded 2026-11-12 for the amended candidate list (see the amendment's Result).

## Context
The model read must return a fixed JSON schema for every message. It runs on a separate
OpenRouter key with a $10 limit that OpenRouter enforces per key. So the candidates are cheap
models that OpenRouter lists as supporting JSON-schema output. The list stops at the mini
tier; no frontier model is a candidate.

## Candidates, in price order
Prices are US dollars per million tokens (prompt / completion), read from OpenRouter's public
models list on 2026-11-11. The order uses an estimated call of 1,500 prompt tokens and 150
completion tokens. Reasoning models may spend more completion tokens than that (unverified).

| # | Model | Prompt | Completion |
|---|---|---|---|
| 1 | mistralai/mistral-nemo | 0.029 | 0.030 |
| 2 | openai/gpt-oss-20b | 0.030 | 0.130 |
| 3 | deepseek/deepseek-v4-flash-0731 | 0.043 | 0.300 |
| 4 | openai/gpt-5-nano | 0.050 | 0.400 |
| 5 | mistralai/mistral-small-3.2-24b-instruct | 0.100 | 0.300 |
| 6 | qwen/qwen3-30b-a3b-instruct-2507 | 0.100 | 0.300 |
| 7 | openai/gpt-4.1-nano | 0.100 | 0.400 |
| 8 | google/gemini-2.5-flash-lite | 0.100 | 0.400 |
| 9 | anthropic/claude-haiku-5.5 | 0.100 | 0.500 |
| 10 | openai/gpt-4o-mini | 0.150 | 0.600 |
| 11 | openai/gpt-oss-120b | 0.150 | 0.600 |
| 12 | openai/gpt-5-mini | 0.250 | 2.000 |

## Decision
- One live pass runs every candidate on seed 7.
- **Gates:** unreadable answers (output that fails the schema even after the parse fallback)
  at most 2%, every safety floor met, and model read latency p95 at most 6 seconds.
- **Pick:** among models that pass the gates, the cheapest that is not clearly worse than the
  best. "Clearly worse" means the best model's 95% interval on "message fully right" (every
  gold field correct) sits entirely above this model's score.
- The chosen model then runs seed 11 for the per-field choice and seed 13 once to confirm.

## Prediction (pre-registered)
1. At least eight of the twelve candidates pass every gate.
2. At least one candidate fails the unreadable-answer gate despite being listed with
   JSON-schema support.
3. The pick is one of candidates 3 to 8. The two cheapest are clearly worse on "message
   fully right".
4. The whole pass on seed 7 spends under $3.

## Amendment, 2026-11-12: one candidate, Claude Haiku 5.5 (committed before any result)

- **Decision (Cristian, 2026-11-11):** the candidate list is Claude Haiku 5.5 alone, called
  directly on the Anthropic API, so the pass runs on his Anthropic credits instead of an
  OpenRouter key. The twelve OpenRouter candidates above are not run.
- **The candidate:**

  | # | Model | Prompt | Completion |
  |---|---|---|---|
  | 1 | claude-haiku-5-5 | 0.100 | 0.500 |

  Prices are US dollars per million tokens for prompts up to 100,000 tokens (Firstcall's are
  about 1,500). Source: Anthropic's published price for Claude Haiku 5.5 ($0.10 input, $0.50
  output per million tokens; $0.50 and $2.50 above 100,000 prompt tokens), the same figures
  the sibling Ledgerline project charges at (`ledgerline/config.py`, read 2026-11-12) and
  OpenRouter listed for `anthropic/claude-haiku-5.5` in the table above.
- **Gates, unchanged:** unreadable answers at most 2%, every safety floor met, and model
  read latency p95 at most 6 seconds. They are judged on the model-only run on seed 7.
- **Pick rule, unchanged:** among models that pass every gate, the cheapest that is not
  clearly worse than the best. With one candidate, the pick is Haiku 5.5 if it passes every
  gate, and no model if it does not. A test applies the rule to the seed-7 pass report.
- **If Haiku 5.5 misses a gate,** the work stops and is called back to Cristian. No bigger
  model is tried.
- **Run order:** seed 7 (the pass and the gates), seed 11 (the per-field choice, written to
  `config/field_methods.yaml`), then seed 13 once (the confirm run). Every live call goes
  through record mode under a run budget of at most $10; the reports are made from the
  recordings.

### Prediction for the amendment (pre-registered)

1. Haiku 5.5 passes every gate on seed 7.
2. Unreadable answers are under 1%, with the API's JSON-schema output.
3. Model read latency p95 is under 4 seconds.
4. The three seeds together spend under $1.

### Result (2026-11-12)

From the [seed-7 pass report](../../evals/reports/2026-11-12-model-seed-7.md), model-only run.

| Gate | Bound | Claude Haiku 5.5 | Met |
|---|---|---|---|
| Unreadable answers | <= 2% | 0/500 | yes |
| Every safety floor | met | 0 wrong attaches, 0 wrong merges, 0 injections taken, 0 agent-contact leaks | yes |
| Model read latency p95 | <= 6 s | 1.42 s | yes |

Message fully right: 486/500 = 0.972 [0.954, 0.983]. The API accepted Firstcall's schema as
structured output, so no answer used the plain-JSON fallback.

**Pick:** claude-haiku-5-5. It passes every gate, and it is the only candidate.
`tests/test_model_choice.py` applies the pick rule to the pass report.

Predictions:
1. **Passes every gate on seed 7:** held.
2. **Unreadable under 1%:** held, 0 of 500.
3. **Latency p95 under 4 seconds:** held, 1.42 seconds.
4. **Under $1 for the three seeds:** see the build log for the measured total.
