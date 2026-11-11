# 0001: rules-only misses on seed 11

Run: the rules-only eval on held-out seed 11,
[report](../../evals/reports/2026-11-11-rules-seed-11.md). Rules-only means no model is read; in
the product this is the fallback path when the model is down, over budget, switched off or has
no key. Numbers are for seen layouts unless the entry says otherwise. Seed 11 found the first
two problems below. Seed 7, the tuning seed, could not: it has no unseen layouts, and its
injection hits all looked like ordinary fail-open results.

## 1. A blank name counted as a compatible name

**What happened.** The first matching rule let a phone match join a lead when the first names
were equal or either side was blank. The rules read no name on unseen layouts, so leads from
those layouts were nameless, and any named message on the same phone could join them. It
misfired in two separate households:

- `s11-0079`, a call from Tina Wood, joined the nameless lead from `s11-0077` (Gregory Wood,
  forwarded from an unseen portal). Then `s11-0288`, Tina's next call, joined that wrong lead:
  1 wrong merge of 115 hard negatives, and 1 wrong attach of 70 repeat messages.
- `s11-0331`, a repeat from Lindsay Herman that matched her own lead by email, phone and name,
  was refused. A nameless lead from `s11-0091` (Laura Herman, unseen portal) on the same phone
  counted as a second compatible hit. That was 1 of the 2 recall misses.

**The fix (approved plan amendment).** A message that has a name never joins a nameless lead on
phone alone; a nameless message still joins. After seed 11 was rerun once with the fix:

| Seen layouts | Before | After |
|---|---|---|
| Wrong attaches | 1 of 70 | 0 of 70 |
| Wrong merges on hard negatives | 1 of 115 | 0 of 115 |
| Recall | 68/70 | 68/70 |
| Correct team, when knowable | 258/259 | 257/259 |

**What it cost.** Recall stays at 68/70, but the misses are now different, and both are safe:
each makes a new lead flagged as a possible duplicate instead of joining.
- `s11-0132` is a named form post on the phone of `s11-0048`, a call with no caller ID and no
  name.
- `s11-0433` is a named call on the phone of `s11-0233`, an unseen-layout form whose name the
  rules did not read.
`s11-0132` has no item, so its new lead sits in Unassigned; that is the second placement miss.

Seed 7 is unchanged by the fix: recall 72/72, 0 of 154 hard-negative merges. The stricter
"both names present" rule was not taken, because it loses two seed-7 repeats.

**Limit.** The rerun uses a seed that had already been looked at, so it shows the fix works on
the cases that found it; it is not a fresh test. Seed 13 is that test and has not run.

## 2. The injection count measured ordinary errors too

**What happened.** The first injection metric counted a hit whenever the attacked field held
the attacker's value: 5 of 30 on seed 11, 8 of 34 on seed 7. Every hit was a "classify this as
a lead" attack on a vendor, support or spam message. Rules decide category from the sender list
and the honeypot alone, and call everything else a lead, so they reached "lead" with or without
the attack. The text did not cause any of them.

**The fix (approved plan amendment).** Each injected message now has a clean twin, the same
message without its attack sentence, in `injection_twins.jsonl`. A hit must equal the attacker's
value and differ from the twin's result. The eval processes each twin just before the real
message, against the same leads, then rolls it back. New count: 0 of 30 on seed 11, 0 of 34
on seed 7. The old count is kept on a second line of each report.

**Limit.** Every seed uses the same six attack sentences
(`injection_text` in `firstcall/corpus/generate.py`), one per attacked field and value, with only the team
name and the phone's formatting varying. Zero hits shows the rules ignore these six. It says
nothing about other phrasings, and a model in PR1b may react to wording these do not cover.

## 3. Category accuracy and spam classed as lead

Category accuracy is 334/434 = 0.770 against a floor of 0.92. Spam classed as lead is 13/58 =
22.4% against a floor of 10%. Both sit inside the ranges predicted before any result:
0.75 to 0.85 in ADR-001, and 15% to 35% in ADR-006. Rules call only spam, so all 43 support and
44 vendor messages count as leads, and spam from a sender not on the source's list, without a
filled honeypot, is a lead too. This is the fail-open design: a lost lead is the worse error,
and no lead was classed as not a lead. The model read is meant to fix category.

## 4. Names on unseen layouts: 0 of 66

On the two unseen portal layouts and the unseen form shape, rules read phone, email and item
on 66/66 but the name on 0/66. Rules know what a phone, an email and a listed item look like,
but not where a new layout puts the name. This also caused the matching misses in entry 1.

**Not fixed, on purpose.** Seeds 11 and 13 share the unseen layouts, so a rules pattern added
for them would be tuned on the held-out seed and would taint the confirm run. The model read is
meant to fill names; the PR1b reread job fills names on leads already created.

## 5. After the code review: hard negatives inside the team, and the safety rerun

The code review found that hard negatives ignored the team boundary: a household, agent,
relay sender or tracking number shared with a lead in another team never met that lead,
because matching looks inside the placed team. Those cases were counted without testing
anything. The gold was regenerated so that every hard negative has an earlier lead in its own
matching pool, and seeds 7 and 11 were rerun once with all the review fixes. Seed 13 has not
run.

| Seen layouts | Seed 11 before | Seed 11 after | Seed 7 before | Seed 7 after |
|---|---|---|---|---|
| Wrong attaches | 0 of 70 | 0 of 70 | 0 of 72 | 0 of 72 |
| Wrong merges on hard negatives | 0 of 115 | 0 of 103 | 0 of 154 | 0 of 131 |
| Injection (old count) | 0 of 30 (5) | 0 of 30 (7) | 0 of 34 (8) | 0 of 34 (8) |
| Agent contact used as the buyer's | 0 of 46 | 0 of 46 | 0 of 46 | 0 of 46 |
| Recall | 68/70 | 66/70 | 72/72 | 71/72 |

The "before" hard-negative counts included cases that could never merge; the "after" counts
are smaller but every case now meets a competing lead. The safety results hold on the harder
set.

The recall misses are all flagged new leads, never joins:
- Seed 11, four named calls: `s11-0080`, `s11-0088`, `s11-0195`, `s11-0346`. Each repeats a
  lead from an unseen portal layout, where the rules read no name, so the amended rule refuses
  the phone-only join (entry 1).
- Seed 7, one call with no name: `s07-0231`. Its phone is shared by a household and a conflict
  lead in the same team, so a nameless phone match has more than one candidate and is flagged
  instead of guessed.

