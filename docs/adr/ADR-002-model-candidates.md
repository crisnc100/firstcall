# ADR-002: Model candidates and the pick

- **Status:** Candidate list and prediction committed 2026-11-11, before any model result.
- **Result:** pending until the model pass runs on the Firstcall key.

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
