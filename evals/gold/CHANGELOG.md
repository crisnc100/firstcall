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
