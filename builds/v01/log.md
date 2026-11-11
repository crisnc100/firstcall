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
