# ADR-003: Matching rules and hard negatives

- **Status:** Prediction committed 2026-11-11, before any matching code or result.
- **Result:** pending.

## Context
A later message from a known buyer must join that buyer's lead, or the team works the same
person twice. A wrong join is worse: it hides a new buyer inside someone else's lead. So
matching must stay strict about what counts as the same person.

## Decision
- An **identity** is the buyer's normalised phone (E.164) or lowercased email.
- Inside the placed team, a message joins a lead with the same email, or the same phone and a
  compatible name (first names equal, or one side blank). With no team, the same lookup runs
  across the org: exactly one hit joins that lead and its team; more than one makes a new lead
  in Unassigned, flagged with the possible matches.
- **Never an identity:** agent phones and emails, portal relay senders listed on the source,
  and call-tracking numbers listed on the source.
- **Same phone, different first name** (a shared household phone) makes a new lead flagged as
  a possible duplicate.
- **Phone points to one lead and email to another:** no join; a new lead is flagged as a
  possible duplicate of both.
- Matching and lead creation hold Postgres advisory locks on each identity key, taken in
  sorted order, so two messages from a new person at once make one lead.
- Matching runs before the category gate, so a known buyer's message joins the lead whatever
  its category. A buyer's "thanks" is never dropped.

## Prediction (pre-registered)
Rules-only, seed 11, seen layouts:
1. Zero wrong attaches among repeat messages, and zero wrong merges on each of the five
   hard-negative kinds (shared agent phone, household phone, portal relay sender,
   call-tracking number, phone and email pointing to different leads).
2. Recall (repeat messages joined to their lead) is at least 0.90. The misses come from
   repeats whose only identity the rules failed to read.
3. On unseen layouts recall drops below 0.85, with still zero wrong attaches.
4. Ten concurrent messages from one new person make exactly one lead in each of 20 runs.
