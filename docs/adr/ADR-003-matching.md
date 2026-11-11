# ADR-003: Matching rules and hard negatives

- **Status:** Prediction committed 2026-11-11, before any matching code or result.
- **Result:** recorded 2026-11-11 for the rules-only eval; prediction 1 was falsified, and the
  compatible-name rule was amended (see Amendment).

## Context
A later message from a known buyer must join that buyer's lead, or the team works the same
person twice. A wrong join is worse: it hides a new buyer inside someone else's lead. So
matching must stay strict about what counts as the same person.

## Decision
- An **identity** is the buyer's normalised phone (E.164) or lowercased email.
- Inside the placed team, a message joins a lead with the same email, or the same phone and a
  compatible name (first names equal, or one side blank; amended below). With no team, the same lookup runs
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

## Result
Rules-only, from the [seed-11 rules-only report](../../evals/reports/2026-11-11-rules-seed-11.md) (rules frozen before it ran), before any change to the matching rule.

1. **Zero wrong attaches and zero wrong merges on each hard-negative kind (seen):**
   falsified. There was 1 wrong attach of 70 repeat messages, and 1 wrong merge of 115 seen
   hard-negative messages: a household phone. That message is also labelled with the
   call-tracking kind, because it is a call; the tracking number played no part.
2. **Recall at least 0.90, misses from unread identities:** the number held, at 68/70 = 0.971.
   The cause did not: neither miss came from an unread identity.
3. **Unseen recall below 0.85, still zero wrong attaches:** falsified on recall (2/2), held on
   wrong attaches (0). Two repeat messages are too few to say anything.
4. **Ten concurrent messages make one lead in each of 20 runs:** held
   (`tests/test_concurrency.py`).

All three matching misses come from one rule: "one side blank" counted a nameless lead as a
compatible name. The rules read no name on unseen layouts, so leads from those layouts were
nameless. Seed 11 showed it in two households:
- `s11-0079` (a different first name, the same phone) joined the nameless lead from
  `s11-0077`. Then `s11-0288`, a repeat of `s11-0079`, joined that wrong lead.
- `s11-0331`, a repeat that matched its own lead by email, phone and name, was refused. A
  nameless lead from `s11-0091` on the same phone counted as a second compatible hit.

Seed 7 has no unseen layouts, so the tuning run could not show this.

## Amendment (2026-11-11, approved by Cristian)
The compatible-name rule is now one-directional: a phone match joins when both first names are
equal, or when the message has no name. A message that has a name never joins a nameless lead
on phone alone. A nameless message still joins, because calls without caller ID are common and
the phone is all they carry.

The stricter variant, "both names present and equal", was rejected: on seed 7 it loses two
gold repeats whose earlier lead had no readable name.

Seed 11 found this rule. The result above is from before the change. The seed-11 report was
then rerun once with the new rule, so it now shows the rule's effect on a run that has already
been looked at. It is not a fresh held-out test. Seed 13 stays untouched and is that test.

After the change, seed 11 shows 0 wrong attaches of 70 and 0 wrong merges of 115, with recall
still 68/70. The two remaining misses are now flagged new leads, not joins. Seed 7 is unchanged.
[docs/failures/0001](../failures/0001-rules-only-seed-11.md) has the details.
