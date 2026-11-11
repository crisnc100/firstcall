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
