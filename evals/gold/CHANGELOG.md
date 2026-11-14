# Gold changelog

Gold labels change only with an entry here that gives the reason. The manifest test fails on
any change to a gold file that the manifest does not record.

## 2026-11-11
- Seeds 7, 11 and 13 generated, before any extraction code existed.

## 2026-11-11, second entry
- A household member's call carried no email, but its label listed one. Calls now never get
  an email in the plan, so the label matches the message. Repeats of those leads are planned
  from the contacts the lead really has, which changes the random draws after them, so all
  three seeds were regenerated. A new test checks that every gold phone, email, name and item
  appears in its message. No rules code had run on seeds 11 or 13.

## 2026-11-11, third entry
- A conflict message (phone of one lead, email of another) could name two leads in different
  teams. Matching looks inside the placed team, so such a message correctly joins the one
  lead visible there, while its label said "new lead". Conflicts now take both leads from one
  team and an item from that team, so the label follows the matching rule. Found on the seed-7
  tuning run; all three seeds were regenerated. No rules code had run on seeds 11 or 13.

## 2026-11-11, fourth entry
- Added `injection_twins.jsonl` to each seed: for each of the 34 injected messages, the same
  message with its attack sentence removed. The injection metric now counts a hit only when
  the attack changed the field, which needs a message to compare against. The twins are built
  from the rendered messages without drawing random numbers, so `messages.jsonl` and
  `labels.jsonl` are byte for byte unchanged in all three seeds, and so are their manifest
  hashes. The manifest gains one entry per seed.

## 2026-11-11, fifth entry
- The PR1a code review found that hard negatives ignored the team boundary. Matching looks
  inside the placed team, so a household phone, agent, relay sender or tracking number shared
  with a lead in another team never met that lead, yet it was labelled and counted. Households
  now take the team of the lead whose phone they share; agents, portals and tracking numbers
  lean towards a favourite per team; and a hard-negative label needs an earlier, different
  lead in the same matching pool. Every seed now has at least 22 same-pool cases of each kind.
  All three seeds were regenerated, with their clean twins. Seed 13 has still never been run.

## 2026-11-13
- Added the note scripts for the note eval: `notes/seed-7` (tuning) and `notes/seed-11` (held
  out), 150 scripts each with gold labels, and their own manifest, `notes/MANIFEST.json`. They
  come from their own random stream, so every message, label and twin file is unchanged. The
  note scorer (`firstcall/evaluation/note_score.py`) and ADR-007 were committed with them,
  before any note reader code. From here the scorer changes only with an entry in this file,
  and never once the seed-11 note run has started.

## 2026-11-14
- The phases 1-3 code review of PR3a found two gold faults in the note scripts and one in the
  scorer. No note reader exists and no model has read a note script, so all three were fixed
  before any run.
  - "Prefers a corner lot." is a stated preference, but its gold need could be empty. The gold
    now marks need present with the term "corner lot". Need goes from empty to present in 2
    seed-7 and 7 seed-11 labels; 8 and 6 labels that already had a need gain the term. Seed 11
    has need empty in 37% of scripts, inside the 35 to 50% rule.
  - A number style with no form for an amount's size falls back to "million" or "k", but the
    label recorded the style asked for. Labels now record the style written. Five seed-11
    scripts written only in seen styles were marked unseen and are now seen: 60 seen and 90
    unseen (60% unseen; the rule is at least 40%). In all, 13 seed-7 and 24 seed-11 labels change
    their style id.
  - The scorer scaled every script number by a thousand and a million, so "two million"
    passed against "two dogs". It now scales only by a scale the script itself writes.
  - No script text changed in either seed: `scripts.jsonl` hashes are as before. Both
    `labels.jsonl` files were regenerated and the notes manifest records their new hashes.

## 2026-11-14, second entry
- The fix check found the scorer change above incomplete: a written "$450k" was scaled again
  to 450 million, and any scale in the script scaled every other number, so "two dogs"
  beside "$450k" supported a two-thousand budget. Each script number now supports only its
  written value, and a bare number joined to a scaled one ("380 and 420 thousand") shares
  that scale. No gold file changed; the gold still meets every floor and the baselines
  still miss them. No note reader or model run exists yet.

## 2026-11-14, third entry
- A third review round found the scorer still read script and answer differently (an
  answer's "between 425 and 450 thousand" was flagged) and let number words run across
  punctuation ("nine hundred thirty thousand; three bedrooms" read as 930003). Script and
  answer now go through one reader: it splits at punctuation and sentence ends, reads digits
  and number words in one pass, and gives a bare number under a thousand the scale of a
  scaled number it is joined to. A regression table checks 25 faithful rewrites of gold
  scripts from both seeds (every range form and written-number style) and 10 invented
  numbers, and every script in both seeds copied through and restated from its gold. No
  gold file changed; the gold still meets every floor and the baselines still miss them.
