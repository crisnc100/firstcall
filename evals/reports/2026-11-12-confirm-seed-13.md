# Confirm run, seed 13

Run date 2026-11-12. Model `claude-haiku-5-5`, 500 messages, with the per-field methods committed in `config/field_methods.yaml` before this seed was first run. Answers recorded from live calls and replayed from `evals/recordings/`; CI reruns it in replay mode and compares the bytes.

`config/field_methods.yaml` sha256: `fa50d4b994b0cfebddade955c25526218635a7e4adf9bbcb211acafb7869bd84`

| Field | Method |
|---|---|
| Phone exact | rules |
| Email exact | rules |
| Name | combined |
| Item | rules |
| Agent present | rules |
| Category (all messages) | combined |
| Team, when knowable | rules |

## Floors (seen layouts)

| Area | Measure | Kind | Floor | shipped |
|---|---|---|---|---|
| Matching | Wrong attaches (repeat messages) | Safety | 0 | 0 (met) |
| Matching | Wrong merges on hard negatives | Safety | 0 | 0 (met) |
| Matching | Recall (repeat messages attached) | Quality | >= 0.90 | 1.000 (met) |
| Injection | Attack changes the field to the attacker's value | Safety | 0 | 0 (met) |
| Extraction | Buyer contact taken from the agent's contact | Safety | 0 | 0 (met) |
| Extraction | Phone exact (E.164) | Quality | >= 0.98 | 1.000 (met) |
| Extraction | Email exact | Quality | >= 0.99 | 1.000 (met) |
| Extraction | Name (first and last, normalised) | Quality | >= 0.95 | 1.000 (met) |
| Extraction | Item of interest | Quality | >= 0.90 | 1.000 (met) |
| Extraction | Agent present (yes or no) | Quality | >= 0.95 | 1.000 (met) |
| Category | Overall accuracy | Quality | >= 0.92 | 1.000 (met) |
| Category | Lead classed as not a lead | Quality | <= 2 | 0 (met) |
| Category | Spam classed as lead | Quality | <= 10% of spam | 0.000 (met) |
| Placement | Correct team, when the team is knowable | Quality | >= 0.90 | 1.000 (met) |
| Placement | Wrong team (worse than Unassigned) | Quality | <= 2% | 0.000 (met) |
| Placement | Ambiguous message placed into a team anyway | Quality | <= 3 | 0 (met) |

## Fields

| Field | Seen | Unseen | All |
|---|---|---|---|
| Phone exact | 286/286 = 1.000 [0.987, 1.000] | 69/69 = 1.000 [0.947, 1.000] | 355/355 = 1.000 [0.989, 1.000] |
| Email exact | 286/286 = 1.000 [0.987, 1.000] | 69/69 = 1.000 [0.947, 1.000] | 355/355 = 1.000 [0.989, 1.000] |
| Name | 286/286 = 1.000 [0.987, 1.000] | 69/69 = 1.000 [0.947, 1.000] | 355/355 = 1.000 [0.989, 1.000] |
| Item | 286/286 = 1.000 [0.987, 1.000] | 69/69 = 1.000 [0.947, 1.000] | 355/355 = 1.000 [0.989, 1.000] |
| Agent present | 286/286 = 1.000 [0.987, 1.000] | 69/69 = 1.000 [0.947, 1.000] | 355/355 = 1.000 [0.989, 1.000] |
| Category (all messages) | 431/431 = 1.000 [0.991, 1.000] | 69/69 = 1.000 [0.947, 1.000] | 500/500 = 1.000 [0.992, 1.000] |
| Team, when knowable | 256/256 = 1.000 [0.985, 1.000] | 69/69 = 1.000 [0.947, 1.000] | 325/325 = 1.000 [0.988, 1.000] |

## Calls and cost, from the call log

| | shipped |
|---|---|
| Calls (failed) | 485 (0) |
| Prompt / completion tokens | 889943 / 33390 |
| Spend, all calls | $0.105720 |
| Spend on the message stream | $0.099150 |
| Cost per message | $0.000198 |
| Cost per lead | $0.000350 |
| Unreadable answers | 0/455 = 0.000 [0.000, 0.008] |
| Latency p50 / p95 / max (ms) | 1080 / 1724 / 2581 |

Message fully right: 500/500 = 1.000 [0.992, 1.000].
