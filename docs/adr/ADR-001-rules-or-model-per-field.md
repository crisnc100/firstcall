# ADR-001: Rules or model, per field

- **Status:** Prediction committed 2026-11-11, before any rules code or eval result exists.
- **Result:** rules-only part recorded 2026-11-11 (see Result). The model and combined parts
  are pending until the model pass.

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

## Result
Rules-only, from the [seed-11 rules-only report](../../evals/reports/2026-11-11-rules-seed-11.md) (rules frozen before it ran). Seen layouts have 289 lead messages and
unseen layouts have 66.

1. **Phone at least 0.97, email at least 0.98 (seen):** held. Both are 289/289 = 1.000.
2. **Name between 0.85 and 0.95 (seen):** falsified. Name is 289/289 = 1.000, above the range.
3. **Item at least 0.93 (seen):** held, 289/289 = 1.000.
4. **Agent present between 0.90 and 0.98 (seen):** falsified. It is 289/289 = 1.000, above the
   range.
5. **Category between 0.75 and 0.85 (seen):** held, 334/434 = 0.770. Every vendor and support
   message is classed as a lead, by design.
6. **Unseen: name below 0.60, phone and email each at least 5 points lower:** half held. Name
   is 0/66. Phone and email are 66/66 = 1.000 on unseen layouts, so they did not fall.
7. **Unseen item within 5 points of seen:** held, 66/66 = 1.000.
8. **The per-field choice:** pending until the model pass.

The seen layouts are labelled, and the rules were written for exactly those labels, so the
seen scores say little about real mail. Unseen names show where rules stop: they know the
shape of a phone, an email and a listed item, but not where a new layout puts the name.
