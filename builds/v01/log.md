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
