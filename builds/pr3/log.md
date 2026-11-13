# PR3 build log

## Phase 1: note gold, ADR-007 and the frozen scorer

- **Where the gold lives.** `evals/gold/notes/seed-{7,11}/{scripts,labels}.jsonl`, with its
  own manifest, `evals/gold/notes/MANIFEST.json`. The plan said "manifest entries"; the
  message manifest test pins each seed folder to exactly three files, so a separate folder
  and manifest keep every existing test and gold file unchanged.
  `firstcall corpus notes --seed N` writes them.
- **Scripts.** 150 per seed: 60 with a completed contact (15 with two kinds), 15 of each hard
  non-action kind, 15 with neither. Half the action scripts also carry one non-action
  sentence, so a reader has to tell them apart inside one note. Each BANT field is present in
  87 of 150 (42% empty). Budgets come in five seen number styles (`$450k`, `$450,000`,
  `450 thousand`, words, `$1.2 million`) and three unseen ones (`450K`, `450 grand`,
  `$1.25M`).
- **Held-out phrasing.** In seed 11 each sentence takes an unseen phrasing or number style
  with a 20% chance; 95 of 150 seed-11 scripts have at least one. Seed 7 has none, and a test
  checks that each unseen phrasing's fixed words never appear in seed 7.
- **Gold the script supports.** Every key term, budget figure (read by the scorer's own
  number reader) and month is checked against its script. Some notes never name the buyer, so
  `buyer` is null there (21 scripts in each seed).
- **Scorer** (`firstcall/evaluation/note_score.py`), as the plan states. Two choices made
  while writing it, plus one limit:
  - Word numbers are read only outside digit numbers, so "1.5 million" is not also one
    million, and "and" joins word numbers only after "hundred" ("four hundred and fifty"),
    so "430 thousand and 505 thousand" stays two numbers.
  - The name list is Faker's en_US first and last names less months, weekdays and 150
    names that are everyday words (`EVERYDAY_NAMES`), so "Price", "Park" or "Cash" in an
    answer is not an invented person.
  - A field is a copy when it holds 80% of the script's distinct words; on a very short note a
    short field can trip it, which the note scripts (two to seven sentences) do not reach.
- **Order check.** `test_note_gold_and_the_scorer_were_committed_before_any_reader_code`
  reads git history for `firstcall/notes/reader.py`, where ADR-007 says the reader lives.
- **Baselines.** Always fill, never fill and copy through each miss the floors on both seeds;
  the gold written as an answer meets every floor with zero unsupported notes.
- **Ruff.** `S311` is ignored for `note_score.py` in pyproject: the bootstrap uses a seeded
  `random.Random` so the report reproduces.
- verify.sh: 652 passed; `note_score.py` branch coverage 98%.
