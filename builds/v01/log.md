# PR1a build log

Deliberate choices made while building PR1a, with the reason for each.

## Phase 1: scaffold, CI, definitions, schema

- **Tables beyond the plan's list.** `agents` (agents are listed per org), `activities` (an
  activity is unique on its message) and `heartbeats` (worker and scheduler healthchecks) are
  added. `orgs` and `heartbeats` are the only tables without `org_id`; a test pins that list.
- **Lead states.** The check constraint already allows all six states from the definitions, so
  PR2 adds transitions without a constraint change. PR1a only writes `pool`.
- **duplicate_delivery is an answer, not a row.** A repeat delivery returns 200 with that status
  and the original message id; nothing is stored. The message status check lists the five
  stored terminal statuses.
- **Arrival time** is `clock_timestamp()` at insert, the database clock.
- **HMAC secrets** live in an environment variable named on the source, never in the database.
  Path tokens and basic-auth passwords are stored hashed.
- **Web.** A minimal Vite + TypeScript package with one page, so verify runs `npm ci` and the
  TypeScript build from the first PR. No Playwright until PR2 has pages to test.
- **Compose.** Postgres, migrate, api and Mailpit now; worker and scheduler join with the jobs.
  ffmpeg waits for PR4, the first code that reads audio.
- **CI database.** CI tests run against a Postgres service (`FIRSTCALL_DATABASE_URL`); compose
  still starts its own stack for the fault suite.
- **Zero skips** is enforced by a pytest session hook: any skip or xfail fails the run.
- **Clean-room scan.** A test hashes every word in committed text and fails on the hashed names
  of the inspiring tool, so the names never appear in the repo.

## Phase 2: synthetic generator, gold, manifest, ADR predictions

- **Committed before any extraction code.** This commit holds the generator, the three seeds,
  the manifest and ADR-001 to ADR-006 with predictions only. `tests/test_corpus.py` checks
  from `git log` that each of them was added in a commit that is an ancestor of, and not the
  same as, the first commit adding `firstcall/core/read.py`, `match.py` or `place.py`. CI
  checks out the full history for this.
- **Note scripts are not in this manifest.** They are tagged for PR3, which adds them and
  their hashes before the first note-reading code, the same way PR4 handles audio.
- **The demo world is one file.** `config/demo.yaml` holds the demo company's teams, items,
  agents and sources. The generator reads it, and the seed command will load it, so the
  corpus and the pipeline cannot drift apart.
- **Unseen layouts:** the Gablework and Lattice Lane portals (direct and forwarded) and the
  partner form's version-2 shape. Seeds 11 and 13 have 65 and 66 unseen messages; seed 7 has
  none.
- **Gold leaves out what no rule could know.** A household member always gives a name, since
  a nameless call from a shared phone matches the other person by the definition. Repeat
  messages never target a lead whose phone or email a later conflict message reused.
- **Hard-negative labels are computed, not assigned.** Every lead message from an agent,
  relay sender or tracking number that an earlier, different lead already used carries that
  label, so counts exceed the 20 minimum.
- **Model candidates (ADR-002)** come from OpenRouter's public models list, read on
  2026-11-11 without a key: models listed with structured outputs, no batch or alias ids,
  ordered by an estimated 1,500-in, 150-out call.
- **Re-deliveries are not in the corpus.** Delivery-key rules are tested directly (F3).

## Phase 3: intake adapters, auth, limits, raw store

- **Routes.** `POST /intake/{source}` for basic auth and HMAC, `POST /intake/{source}/{token}`
  for path tokens. A token on a non-token source, or no token on a token source, is 401.
- **Order of checks:** per-IP rate, unknown source (404), per-source rate (429), content type
  (415), declared length (413), token or basic auth (401), the capped body read (413), then the
  HMAC check (401), which needs the body. The cheap refusals come before any body is read.
- **HMAC format:** `Firstcall-Timestamp: <unix seconds>` and `Firstcall-Signature:
  sha256=<hex HMAC-SHA256 of "timestamp.body">`, refused when more than 5 minutes off.
- **Rate limits** are a sliding one-minute window in process memory. That holds for one api
  process, which is what v0.1 runs.
- **A commit failure answers 503**, so the sender retries; nothing is half written because the
  message and its job share one transaction.
- **Repeat detection** takes an advisory lock on (source, key) before looking, so two copies
  of one delivery arriving together store one message. The 10-minute window is measured on the
  database clock, like arrival time.
- **Secrets.** `firstcall rotate-secret <source>` prints a new token or password once and
  stores only its hash; the old one stops working at once. Seeding never sets a secret.

## Phase 4: rules read, place, match, the pipeline and jobs

- **A gold fix came first.** Scoring the first rules draft on seed 7 showed household calls
  labelled with an email the call never carries. The generator was fixed, all three seeds were
  regenerated with a changelog entry, and a test now checks that every gold value appears in
  its message. That commit landed before any rules code.
- **Rules were tuned on seed 7 only**, through a scratch script that refuses any other seed.
  Seed 7 seen-layout rules scores: name, phone, email, item, agent and team all 1.0; category
  0.80 (rules call only spam). The patterns cover the five seen portal layouts, the two form
  shapes, calls, agent emails, direct emails and both forward styles. For layouts they do not
  know, rules fall back only to value patterns (phones, emails, configured items), never to
  guessed field names, and mark those fields `unsure` for the combined method in PR1b.
- **Steering text.** Free-text scanning skips lines that address an assistant or tell the
  reader to ignore its instructions. Labels and mapped fields are read as they are.
- **Matching details the plan leaves open.** A phone "points to" a lead whatever the name; so a
  household phone plus another lead's email is a conflict, not a join. Two compatible hits in
  the placed team make a flagged new lead, the same as two hits across the org. A hit only in
  another team makes a new lead in the placed team, without a flag.
- **Re-forward check is for email only.** The same form body after 10 minutes attaches as a new
  message (F3); only an email with a new Message-ID and the same cleaned body within 24 hours
  ends `duplicate_content`.
- **One transaction per message.** The read, the lead or activity, its identities, the lead
  event, the message's terminal status and the job's `done` commit together. A crash anywhere
  before the commit leaves only the claimed job, which the reaper returns.
- **Retries** back off 2, 4, 8 and 16 seconds. A job claimed with no attempts left, or reaped
  after its last attempt, ends `failed: retries_exhausted`.
- **Compose** now runs worker and scheduler with heartbeat healthchecks (`firstcall health`).
  The fault suite runs real worker containers against a throwaway database on the compose
  Postgres and asserts the exit code 137 and the failpoint's log line.
- **`find_phones`** relies on the phone matcher's default leniency, which already drops
  invalid numbers, so the module has no dead check to cover.

## Phase 5: the rules-only eval and report

- **A second gold fix, found on seed 7.** Conflict messages could point to two leads in
  different teams, where matching (which looks inside the placed team) correctly joins the
  one lead it can see. Conflicts now draw both leads and the item from one team. Fixed and
  regenerated with a changelog entry before seed 11 ever ran.
- **Seed 11 ran once, with the rules frozen.** After it, only the report itself changed: the
  hard-negative total now counts a message once even when it carries two kinds, and the
  kinds print in a fixed order. Neither touches the rules.
- **Report names** are `evals/reports/<date>-rules-seed-<N>.md` and `.json`, since one date
  holds reports for two seeds. The date is an argument, so CI can rerun a report and compare
  bytes (`scripts/check_replay_report.py`, a verify step).
- **The eval runs the real pipeline** on a throwaway database: each message is stored through
  the intake store and processed by the job runner, one at a time in corpus order. A fixed
  clock keeps event ids and leases out of the report.
- **Seed 13 is refused** until `config/field_methods.yaml` exists, and a test checks no
  seed-13 report is committed.
- **Rules-only safety misses on seed 11, and their cause.** The blank-name rule misfired in two
  separate households. In one, a household phone merge and the wrong attach after it came from
  a lead on an unseen portal layout, where the rules read no name; "one side blank" then
  counted as a compatible name. In the other, a repeat was refused because a second nameless
  lead on the same phone counted as a compatible hit. The advisor's replay found the second
  household; the first version of this entry named only one. The five injection hits are all
  category attacks on non-leads: the rules never read text for category and fail open, so
  spam without a listed sender or honeypot is "lead" with or without the attack.
- **The example form** (`examples/form.html`) posts JSON with a hidden `website` honeypot; a
  test pins its fields to the site-form source.
- **Left for PR1b:** the `--mode` and `--budget` flags, `docs/rerecord.md` (there is nothing
  recorded to re-record yet), cost and latency figures (no model calls).

## Choices agreed with the orchestrator (2026-11-11)

The plan left these four open. Each was proposed with a recommendation and agreed as proposed.

1. **No note scripts in PR1a.** PR3 generates them and adds their hashes to the manifest
   before the first note-reading code. PR1a's manifest covers message inputs and labels for
   seeds 7, 11 and 13.
2. **A minimal web package and Mailpit now.** `web/` is Vite + TypeScript with one page, so
   verify runs `npm ci` and the TypeScript build from the first PR. Mailpit runs in compose
   with its healthcheck. Playwright waits until there are pages to test.
3. **A repeat delivery writes nothing.** It answers 200 with
   `{"status": "duplicate_delivery", "message_id": <original>}`, and no new row is stored.
4. **`firstcall eval --seed N` is rules-only.** It refuses seed 13 until
   `config/field_methods.yaml` exists. `--mode` and `--budget` come with the model gateway.

## Phase 6: two approved amendments after the advisor's review

- **Results recorded first.** ADR-001, ADR-003 and ADR-006 got their rules-only results from
  the frozen-rules seed-11 run before any fix. ADR-003's zero-merge prediction is marked
  falsified. The seen hard-negative count is 115, not the 98 I first reported.
- **Matching amendment.** A named message never joins a nameless lead on phone alone; a nameless
  message still joins. The stricter "both names present" variant was not taken. The two changed
  existing assertions sit in their own test-only commit.
- **Injection amendment.** The clean twins are built by cutting the exact rendered attack
  sentence out of each message, which draws no random numbers, so existing gold bytes and
  hashes stay the same. Each twin is processed inside a rolled-back transaction just before its
  message, so both see the same leads. A case with no twin still counts as a hit. The manifest
  test's exact file set was changed in a test-only commit.
- **Seed 11 reran once** with both amendments; seed 7 reran too; seed 13 did not run. The fix
  swaps the two recall misses for two different ones, both flagged possible duplicates instead
  of joins. Details in `docs/failures/0001-rules-only-seed-11.md`.
- **Not done, on purpose:** no `read.py` patterns for unseen layouts, no name backfill on
  attach, no new injection phrasings.

## Phase 7: the code review fix round

Sol reviewed the code and Fable ran the security pass. Every HIGH and MEDIUM was fixed in one
round, each with a regression test, in `tests/test_pr1a_review.py` (named by finding id). That
file was committed first, and all 27 cases failed before any fix.

- **Read budget, not pattern patches (1c9500 s1).** `read()` checks one budget at its boundary
  (200,000 characters, 4,000 lines, 4,000 characters a line). Over it, the message ends
  `failed` with a reason that names the limit; nothing is cut, because a cut could drop the
  line that names the agent. Inside the budget, every pattern is linear. Label lines are
  parsed with string methods, names use possessive quantifiers, and the email, HTML-tag,
  From-line and forward patterns have bounded counts. One test runs every compiled pattern
  in the core modules over adversarial lines at the cap under a CPU-time limit; another runs
  the whole read at the cap.
- **Form keys stay in the path; the access log goes (575e84 s1).** The plan names a token in
  the URL path for form tools that cannot sign, so uvicorn runs with the access log off rather
  than moving the key to a header. The docs now call it a public form key: a browser page shows
  it, so the honeypot, the rate limits and the spam check protect a form source.
- **Proxy headers.** Uvicorn's own proxy handling is off, so `FIRSTCALL_TRUST_PROXY` is the
  only switch. With it on, the client is the entry `FIRSTCALL_TRUSTED_HOPS` from the right
  (default 1). A test drives the real server.
- **Rate limits.** The per-source budget counts only requests that passed auth. At most four
  argon2 checks run at once. Idle keys are swept each minute, the key table is capped, and
  IPv6 clients key by their /64.
- **CORS** comes from a per-source `allowed_origins` list (migration 0002). The demo site form
  allows `https://www.larkspur.example`.
- **Gold, fifth regeneration (174a3f f1).** Hard negatives now compete inside the matching
  pool, at 22 or more same-pool cases of each kind per seed. Seeds 7 and 11 reran once; seed 13
  did not run. The seed-11 safety floors all stay at zero on the harder set. Recall moved from
  68/70 to 66/70 on seed 11 and from 72/72 to 71/72 on seed 7; every miss is a flagged new
  lead (docs/failures/0001, entry 5).
- **Scoring by lead ownership (177f72 f1).** Each system lead belongs to the gold buyer whose
  message created it. A join to a safe duplicate of the same buyer counts as a join to that
  buyer, not as a wrong attach.
- **Existing-test changes**, each in its own test-only commit:
  - the CRLF test, which converted line endings twice;
  - the server-log test, which also caught the test client's own URL log;
  - one type annotation.

## Phase 8: the fix-check round (last round)

The fix check found five regressions from the review round. Their tests are in
`tests/test_fix_check.py`, committed before the fix, and all failed first.

- **Forward marker, blank line after it.** The marker pattern stops at its own line end, so
  `_forward` skips leading blank lines before reading the forwarded headers.
- **HTML tags.** The bounded tag pattern left tags over 2,000 characters in the text. A scan
  that resumes where the last search stopped replaces every tag whatever its length, and stays
  linear. A guard test times it at the full read budget.
- **Quote-led addresses** are kept as written, because 'ana@ and ana@ can be different people.
  Only an address wrapped in a matching pair of quotes loses them.
- **The rate-limit key cap** evicts only to make room for a new key; a key already in the table
  keeps its history.
- **CORS on a per-IP 429** comes from a per-minute cache of every source's allowed origins, so
  a flood costs at most one query a minute. The example form catches a refused request and
  shows "Please try again."

Whatever the review of this round finds is deferred, not fixed in another round.

## Phase 9: the final slice review

A fresh review of the files changed in phases 7 and 8 found five MEDIUM findings. Their tests
are in `tests/test_final_slice.py`, committed before the fix, and all failed first. The
read-speed test change sits in its own test-only commit.

- **Domains are read whole or not at all.** The domain takes every label without giving one
  back, and nothing matches if more labels follow than the bound. The last label must be
  alphabetic or Punycode.
- **The tag scan knows quotes and comments.** A ">" inside a quoted attribute value, after "=",
  does not end a tag, and a comment ends only at "-->". Each tag is still scanned once.
- **Forwards read Reply-To from the forwarded headers.** The forwarder's own Reply-To is never
  used once a forwarded sender is found.
- **Headers are inside the budget.** Before any parse, the raw header block is capped at 64 KB
  and each unfolded header at 4,000 bytes. Email is parsed with `compat32`, so the structured
  header parser, whose time grows faster than a header's length, never sees message text.
  Intake keys an over-budget message by its bytes, so a malformed Message-ID no longer raises.
- **The read-speed test now reads.** It leaves room under the budget, drops the suppressed
  error and requires a reading from every call. A sabotaged `_plain_lines` now fails it.

Both reports still reproduce byte for byte, so none of these changes moves a corpus result.
This was the last fix round; whatever the fix check finds is deferred.

## Phase 10: the structural email-parsing reset

The fix check on the final slice found four regressions from the compat32 parser (attached
messages read as the body, encoded display names split at a comma, nested part headers
outside the budget, folded headers left folded). The same kind of result had come back twice,
so the approach was set by all four model families, not by another patch.

- **Back to the default parser.** `policy.default`, `get_body` (plain, then HTML) and
  `get_content`, and `message.get` for From, Subject, Date and Reply-To. The quote-aware tag
  scan, the inner Reply-To and the domain fix stay. Intake keeps its bounded headers-only
  `compat32` parse for Message-ID.
- **The budget lives inside the parser.** A policy subclass checks every header of every part,
  nested ones included, as the parser meets it. Limits: 4,000 characters for one unfolded
  header, 64,000 for all headers, 100 parts, and 10 levels of nesting. A stack overflow from
  deeper nesting is caught too. Each limit ends the message `failed` with a reason, and the raw
  bytes stay stored.
- **The root cause was the fixtures.** The corpus is single-part, ASCII, unfolded and
  LF-only, so every parser change replayed byte for byte. `tests/test_email_structure.py`
  adds the shapes real mail has: multipart/alternative, an attached message with a decoy
  number, an encoded display name with a comma, a folded subject, CRLF and bare-CR endings,
  and a long References header.
- **Timing at the caps** (CPU, best of three):

  | Case | CPU | Result |
  |---|---|---|
  | Legal headers filling 63 KB | 0.2 ms | read |
  | Headers just over the total | 0.1 ms | refused |
  | Nested Content-Type just under 4,000 characters | 5.9 ms | read |
  | Nested Content-Type just over 4,000 characters | 0.1 ms | refused |
  | x3 payload (100,000 semicolons) | 0.2 ms | refused |
  | 1,000 nested message layers | 2.8 ms | refused (part count) |

  The caps did not need lowering.
- **Long unread headers are allowed.** The 4,000-character cap applies only to the headers
  that get read or parsed: From, To, Reply-To, Sender, Subject, Date, Message-ID, Content-Type,
  Content-Disposition, Content-Transfer-Encoding and MIME-Version. Others, such as a long reply
  chain's References, count only toward the 64,000 total and the part limit. Losing a real lead
  over its thread history would be the worse error. A 20,000-character References header is
  read with its buyer, inside the 2-CPU-second test limit.

## Phase 11: the last review slice

The review of the email reset found 4 MEDIUM and 4 LOW findings. Their tests are in
`tests/test_last_slice.py`, committed first, and all 13 cases failed before the fix.

- **HTML keeps its lines.** Block tags become line breaks, so HTML-only portal fields in
  paragraphs, br lines or table rows are labelled lines again. Table cells are the one
  deliberate exception to the list I was given: they stay spaces, because a label cell and its
  value cell share a row, and a line break between them would separate "Name:" from the name.
  The row tag still ends the line.
- **Parts are counted where the parser makes them**, in the policy's message factory, so parts
  with no headers count too.
- **Intake and the worker share one header rule.** Only read or parsed headers are capped at
  4,000 characters; every header counts toward the 64 KB total.
- **Long Message-IDs** (over 200 characters, or with unprintable characters) are keyed by their
  hash, so the message is stored and a redelivery still matches.
- **Recursion.** A stack overflow anywhere in a read, including headers the library parses
  only when fetched and deeply nested JSON, ends the message `failed` with a reason on its
  first attempt.

Both reports still reproduce byte for byte.
