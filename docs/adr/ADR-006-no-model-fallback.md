# ADR-006: The no-model fallback

- **Status:** Prediction committed 2026-11-11, before the pipeline or the model read exists.
- **Result:** pending.

## Context
The model can be down, over budget, switched off or missing its key. A lead that waits for
the model is a lead nobody calls. A lead dropped as "not a lead" is worse than a spam message
shown to a salesperson.

## Decision
When the model is unavailable, the lead is still created and alerted from the rules read. The
category fails open: anything the rules do not call spam is a lead. The message is marked
`needs_reread`. A reread job, once the model is back, fills only empty fields; it never moves
an owned lead, closes a lead or changes a field a person edited.

## Prediction (pre-registered)
Rules-only path, seed 11, seen layouts:
1. Zero leads are classed as not a lead, because the rules call only spam.
2. Spam classed as lead is between 15% and 35% of spam: spam from listed senders and
   honeypot hits is caught, the rest is not.
3. Every message ends in a terminal status with a reason.
4. Intake commit to push request on the rules path is under 2 seconds at p95 on compose.
