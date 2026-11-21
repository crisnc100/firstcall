**Verdict up front: needs changes.** The shape is sound, but the plan as written breaks its own CI rule, leaves voice unable to log a contact without a model, and has an eval whose size choice is probably decided before it runs.

Three limits on this review:
- **Nothing was run.** The sandbox has no Python environment, so the scorer claims in item 8 come from reading `note_score.py` and listing seed 11's names.
- **GitHub was unreachable.** I could not see whether the 3-minute CI has landed on the remote.
- **Nothing was saved.** `builds/pr4/bounce.md` is empty and untracked; this review is not written into it.

## What will break

1. **The CI lanes the plan relies on do not exist here, and its own wiring violates its rule.**
   - No local branch has a lane split: `.github/workflows/verify.yml:41-42` is one job running all of `verify.sh`.
   - `verify.sh:32-33` builds and starts every compose service. With a `transcriber` service that defaults to `local` (`plan.md:219`) and fetches its model at start (`plan.md:256`), every PR run builds the voice image and downloads a model.
   - The plan never names the mechanism that prevents this (a compose profile, or replay mode in verify).
   - `verify.sh:2` fails on any skipped test, so "PR if the runner has ffprobe, otherwise slow" (`plan.md:342`) cannot be a skip. Local verify on the Mac also gains an undeclared ffmpeg prerequisite.
   - Pyright covers all of `firstcall` (`pyproject.toml:66`), but `uv sync --locked` (`verify.sh:9`) will not install a `voice` group, so the faster-whisper import fails to resolve.

2. **The transcriber will report unhealthy mid-memo.** `work()` beats only between jobs (`worker.py:110-113`) and health means a beat within 60 seconds (`worker.py:30`). At ADR-005's predicted 60 seconds per audio minute, any memo over a minute crosses that. The plan covers the lease but not the heartbeat. The model download and load also happen before the first beat.

3. **A job is one transaction, and the plan does not split it.** `run()` wraps the whole job in `sessions.begin()` (`jobs.py:122`). For transcription that is a transaction held open for minutes; if it locks the memo row, delete lead (`delete.py:62-82`) blocks behind it. The plan needs to say read, transcribe and write are three steps.

4. **Nothing guards a late finish.** `run()` marks a job done without checking the attempt (`jobs.py:132-136`), and the renew thread has no error handling (`jobs.py:89-100`), so one database blip ends renewal silently. With a second transcriber that gives two notes. Phase 2 only tests a killed process.

5. **Audio retention is unbounded when no transcriber runs.** `reap()` touches only `running` jobs (`jobs.py:176`); a queued job nobody claims never fails. Compose has no restart policy, and a transcriber that cannot reach Hugging Face never becomes ready. F37 passes while audio sits in Postgres for days. v01 dropped the sweep on the premise that deletion always follows quickly.

6. **The rate limit does not do what the plan says.** Six uploads a minute of 3-minute memos is 18 audio minutes queued per minute. At 60 compute seconds per audio minute that is an hour of CPU in about 3.5 minutes, plus 30 MB a minute into Postgres. `plan.md:161-164` claims the opposite. There is no queue-depth cap.

7. **The 3-minute limit can be bypassed with forged timestamps.** ffprobe reads timestamps a crafted file can fake, and the transcriber's re-check comes after decoding into memory (`plan.md:179-185`). Five MB of low-bitrate Opus can be over an hour of audio. That is an out-of-memory kill, which counts as "any other error" and retries five times.

8. **The upload route can stall the api.** A 10-second ffprobe has to run off the event loop, as intake does with `run_in_threadpool` (`api.py:104,141`). The plan does not say so.

9. **Voice cannot log a contact without the model.**
   - The plan removes the action select (`plan.md:44-45`). Contacts are logged only from `add_note` (`notes.py:94`) and from confirming a suggestion (`suggestions.py:134`).
   - The default mode is offline (`settings.py:35`), so a no-key voice memo can never stop contact-due escalation.
   - With a key, a missed action has no fallback. The plan's own test clip heard "Called" as "Cold".
   - v01's reason for no floor on actions ("a wrong suggestion costs one tap") covers precision, not recall.

10. **The lead page does not poll.** `work.ts` has no poller; PR3's page test calls `owner.reload()` (`tests/fault/test_pages_suggestions.py:136,154`). "Transcribing…, then becomes a note" (`plan.md:135-138`) needs new polling the plan never lists.

11. **Smaller unhandled cases.**
    - A transcript over `NOTE_MAX` of 5,000 characters (`notes.py:33`).
    - Safari's MP4 recordings may hit the same missing-duration trap; the plan handles only WebM.
    - "Deleted" also leaves the audio bytes in Postgres's write-ahead log and any backup.

## The eval

12. **The unsupported-fact floor is probably unmeetable by any Whisper size.**
    - Scoring is against the script (`plan.md:315-317`). A name counts as unsupported when it is in Faker's list and not in the script (`note_score.py:230-232,322-336`), and the tidied note is a scored field (`note_terms.py:26-33`).
    - Seed 11 contains both Brian and Bryan, both Stephen and Steven, plus Ashlee, Kristen, Meghan, Philip, Theresa, Tracy, Shelley and Kaylee. No transcriber can pick the spelling by ear.
    - The floor is at most 1 note of 150. Roughly 15 to 20 scripts carry such a name.
    - If so, every size misses and the pick rule falls to "none does, `small.en` ships" (`plan.md:323-325`), which is the default already. ADR-005's first prediction would fail on spelling, not hearing.
    - I did not confirm each variant is in Faker's list.
    - Numbers may have the same problem: "between 425 and $450,000" likely reads as 425.

13. **Seed 11 has already been peeked at.** The two test clips (`plan.md:234-247`) are seed-11 scripts run through all three candidates. The spoken-text rule and the "fixed before any result" settings were then chosen (`plan.md:230,282-283`). It is small, but ADR-005 should disclose it.

14. **There is no dry run before the run-once confirm.** The speech pipeline, the number-to-words rule and the WER scorer all meet real Whisper output for the first time on the committed run. Seed 7 audio would be a free pilot.

15. **The pick has no margin and no speed gate.**
    - `floors()` uses point estimates (`note_score.py:459-477`) on about 87 filled cases per field. A size passing by 0.005 wins, on the same seed that reports the result.
    - The Atlas timing is phase 7, after the pick, optional, with no ceiling stated. If `small.en` is too slow there, nothing in the plan reacts.

## Where the acceptance bar is too weak

16. **PR4a never runs real Whisper in the image it ships.** Every 4a check uses a fake or replay. The plan already saw faster-whisper fail on every clip with the wrong `av` (`plan.md:90-93`). "4a merged alone works" (`plan.md:60-62`) is unproven. The nightly smoke uses `tiny.en`, not the shipped size.

17. **Format fixtures are made by ffmpeg, not by a browser** (`plan.md:296-298`). Chrome's recordings differ exactly where the plan expects trouble. The fake-microphone check only "sees the upload accepted" (`plan.md:398-399`), so no browser-recorded audio is ever transcribed.

18. **F40 can pass with a reload** while a real user watches "Transcribing…" forever (item 10).

19. **F37 passes with audio at rest indefinitely** (item 5).

20. **The pick-rule test is circular** when no size meets the floors (`plan.md:434`).

## Over-built for a portfolio demo

- **Stop-at-first-pass reads.** Phase 5 already transcribes everything free, and reading all six runs costs about $0.25. Dropping the ordering removes a rule, a test, and empty cells in the report.
- **Per-voice and per-number-style WER.** Six voices over 150 clips is 25 clips each, which is noise.
- **The tmpfs scratch folder.** The transcriber already pipes audio to ffmpeg; ffprobe could do the same, removing the folder, its cleanup and its checks.
- **`fetch-model` with its own checksum.** A pinned revision already covers it.
- **A nightly lane for a three-clip smoke.** One logged manual run in the built image before merge proves more.
- **Process tests** such as the piper-not-in-lock check. They are cheap, but they guard ceremony.

The transcriber split, Postgres storage and the two-PR split are not over-built.

**Verdict: needs changes** — fix items 1, 5, 9, 12 and 16 before approval; the rest can be folded into the phases.
