# PR4 build log

# PR4a (branch voice)

## Phase 1: voice gold and its frozen scorer

- **Where the gold lives.** `evals/gold/voice/`, beside the note gold: `seed-11/clean/` and
  `seed-11/degraded/` (150 MP3s each, 16 kHz mono, 24 kbps), `seed-11/clips.jsonl` (voice,
  speed, noise seed and length of each clip), `seed-11/spoken.jsonl` (what each voice read),
  `LICENSES.md`, `name-spellings.json` and its own `MANIFEST.json`, which hashes every other
  file in the folder. `firstcall corpus voice --piper CMD --models DIR` writes it
  (`firstcall/corpus/voice.py`). The message and note manifests and every file they list are
  unchanged.
- **Voices.** Ten, 15 scripts each, one voice per script for both clips: five US English
  (`joe`, `kathleen`, `kristin`, `mike`, `ljspeech`), two UK English (`cori`, `alba`) and
  three VCTK speakers: p245 Irish (Dublin), p326 Australian (Sydney), p237 Scottish (Fife).
  Every model card's data is CC0, CC BY 4.0 or public domain, and so is the model each was
  finetuned from. Left out: non-commercial cards (`semaine`, `hfc_*`, `ryan`, `l2arctic`),
  cards that only link a licence (`alan`, `amy`, `kusal`, `jenny_dioco`, `arctic`), and
  `lessac` with the voices finetuned from it (`northern_english_male`, `libritts_r`), since
  lessac's card links its own Blizzard 2013 licence. The VCTK accents come from a Hugging
  Face listing of VCTK's speaker information; the corpus's own `speaker-info.txt` is inside
  its 10 GB archive and was not downloaded. Models from `rhasspy/piper-voices` at e9c8a394,
  downloaded to a scratch folder and deleted after rendering.
- **Spoken text.** Each figure becomes its full value in words, plus "dollars" where the
  script had "$": "$1.2 million" is "one million two hundred thousand dollars", and
  "450 grand" is "four hundred fifty thousand". The note scorer's reader cannot read "one
  point two million" or a worded "grand", and the plan's check needs every gold figure to
  read back. So the audio carries no number style; ADR-005 says so. Seed 7 passes the same
  read-back, for phase 5. "HOA" needed no rule: espeak spells it out.
- **Degraded.** Piper's own rate at 0.9 or 1.1 (75 each, seeded), white noise at 10 dB
  below the clip's level (seeded per clip), a 300 to 3,400 Hz high-pass and low-pass, then
  an Opus round trip at 12 kbps, then MP3.
- **Names by sound.** `name-spellings.json` holds 45 groups of spellings that sound the
  same, each with a name used in a seed-7 or seed-11 script and only names in the scorer's
  list. Candidates came from three phonetic codes over the scorer's list (jellyfish, a
  one-off tool outside the lock), then were cut by hand to true homophones: Carolyn and
  Caroline, Laura and Laurie, Dennis and Denise, Jaime and Jamie are out. By this table 42 of
  seed 11's 150 scripts hold such a name (the plan counted about 35 with a looser list).
- **Voice scorer** (`firstcall/evaluation/voice_score.py`). WER reuses the note scorer's
  number reader, so the spoken text has WER 0 against its script (a test). The note scorer
  is untouched: voice filters its unsupported findings, keeps the strict count as
  `unsupported_strict`, relabels the floor row "names by sound" and reports the strict count
  as its own line.
- **ADR-005** gains the candidates, settings, speed bound, pick rule, how voice is scored and
  the disclosure of the two hand-written clips. Its prediction is unchanged and its result
  pending. A test reads ADR-005 as it stood at the commit that first added the voice
  manifest.
- **Piper on this Mac.** piper-tts 1.8.0's macOS wheel builds its espeak-ng data path into a
  160-character buffer; under the scratch folder the path is longer, so espeak fell back to
  the CI runner's build path and failed. `ESPEAK_DATA_PATH` pointing at a short symlink to
  the package fixed it. Piper ran from a throwaway venv in the scratch folder; nothing GPL is
  in `pyproject.toml` or `uv.lock`.
