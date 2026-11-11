# ADR-001: Rules or model, per field

- **Status:** Prediction committed 2026-11-11, before any rules code or eval result exists.
- **Result:** pending. The rules-only numbers arrive with the first rules eval; the model and
  combined numbers arrive with the model pass.

## Context
Each message needs a buyer's phone, email and name, the item of interest, whether an agent
wrote it, a category and a team. Fixed rules (regular expressions plus each source's field
mapping) cost nothing and never invent a value. A model reads layouts the rules were never
written for, but it costs money, adds latency and can be steered by text in the message.

## Decision
For each field, three methods are scored on held-out seed 11: rules-only, model-only, and
combined (rules first; the model fills only what the rules leave empty or mark unsure). The
method with the highest accuracy ships. When two are within 1 point, the cheaper one ships,
in the order rules, combined, model. The choice is written to `config/field_methods.yaml`,
and a test checks that file against this rule applied to the seed-11 report. Seed 13 then
confirms the shipped pipeline once.

"Seen" layouts appear in every seed and are the ones the rules are tuned on (seed 7).
"Unseen" layouts (two portal layouts and one form shape) appear only in seeds 11 and 13.

## Prediction (pre-registered)
Rules-only, seed 11, seen layouts:
1. Phone exact (E.164) is at least 0.97 and email exact at least 0.98, because portals and
   forms label these fields and both have a strict shape.
2. Name exact is between 0.85 and 0.95. Calls without caller ID and agent messages, where the
   name sits in a sentence, are the likely misses.
3. Item is at least 0.93, because items come from a fixed list that rules can look up in any
   layout.
4. Agent present is between 0.90 and 0.98: listed agents are easy, unlisted ones depend on
   phrasing.
5. Category accuracy is between 0.75 and 0.85. Rules call only spam, and everything else is a
   lead by design, so every vendor and support message counts as wrong.

Rules-only, seed 11, unseen layouts:
6. Name falls below 0.60, and phone and email each fall by at least 5 points compared with
   seen layouts.
7. Item stays within 5 points of its seen score.

Per-field choice after the model pass:
8. Rules ship for phone and email; combined ships for name and item; the model ships for
   category; team follows from the item and form fields and ships as rules.
