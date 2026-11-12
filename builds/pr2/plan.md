# Firstcall v0.1: plan (rev 3, after the Bounce and the advisor)

## Goal

Firstcall is an open-source lead hub for sales teams that get leads from many places.
It puts every lead in one queue, alerts the right team in seconds, and makes one person
own each lead with a timer running. It keeps that owner on a follow-up rhythm until the
lead ends with a recorded outcome.

The pitch has two halves:
- **No CRM:** Firstcall is the system of record. You can see every lead, who owns it and how it ended.
- **A CRM already:** Firstcall is the fast layer in front of it. Leads are claimed in minutes, and each
  step goes out as a signed, ordered event that a CRM, Zapier, Make or n8n can consume.

Four selling points, each with the number that proves it:

| Point | The number on the dashboard |
|---|---|
| Simplicity: one queue, voice notes instead of typing | Share of contacts logged by voice, and voice-note accuracy from the eval |
| Centralised: every source in one place | Leads by source; duplicates merged, and possible duplicates flagged |
| Ownership: first claim wins, timer running | Time to claim and time to first real contact; unclaimed leads and escalations |
| Monitoring: leadership sees it live | Follow-ups done on time, conversion by source and team, lost reasons |

"Time to log a note" is not shown in v0.1. It needs real users, and the demo has none.

A cost and value page closes the argument. Running cost comes from measured model use per
lead. Business value comes from sliders the reader sets (conversion rate, lift, margin).

**Why it exists:** an applied-AI portfolio project and a usable free beta. It is not for sale yet.

**Clean room:** the idea comes from a lead tool built for one homebuilder. No code, names,
data, screenshots or business rules from that tool are used. "Team" replaces community
everywhere. The follow-up cadence and the buyer profile come from generic public practice
(see Definitions), not from that tool.

**PRs: eight.** Each merges alone, leaves `main` working, and fits one review. Each needs the
code of the one before it, so each branches from `main` after the one before it merges.
- **PR1a** rules, no key: the data model, gold, intake, rules read, place, match, rules-only eval.
- **PR1b** model read and cost: the model pass, the per-field choice, the confirm run, measured cost per lead.
- **PR2** users, claim and board: identity and access, the most security-relevant code.
- **PR3** typed notes, follow-ups and outcomes, with the note-reading eval on text.
- **PR4** voice: local transcription in front of PR3's note reader, with the audio eval.
- **PR5** dashboard, replay demo, cost and value page, and docs.
- **PR6** hosted beta.
- **PR7** CRM events: delivery of lead events to webhooks, and the mini CRM receiver.

The split is by risk and by key. PR1a needs no key, so the foundation is reviewed before any
model work. Voice comes before the beta by Cristian's choice, so the beta shows it. CRM comes
last: the beta does not use it (endpoints are locked there), and it only consumes events that
exist from PR1a, so it can land any time without holding up the showcase.

## Definitions (fixed before any code)

### Who

- **Org:** one company using Firstcall. Every domain table has a non-null `org_id` from PR1a.
  v0.1 runs one org, the demo company. The column exists so isolation per company can come later.
- **Team:** a group of salespeople covering a territory, location or product line. Each team has
  an IANA time zone and business hours (default Monday to Saturday, 09:00 to 18:00 local).
- **User:** a person who signs in with email and password. A user has one or more roles.
- **Roles:**

  | Role | Scope | Can |
  |---|---|---|
  | Salesperson | Their teams | See the team pool and their own leads. Claim, release, note, set outcome on their own leads. |
  | Team lead | Their teams | Everything a salesperson can, on every lead in their teams. Reassign, return to pool, reopen, see the dashboard for their teams. |
  | Monitor | Whole org | See the Unassigned pool and the Not-a-lead tray. Place a lead into a team. No access to notes on team leads. |
  | Admin | Whole org | Everything, plus users, teams, sources, CRM endpoints and deletion. |

- **Access rule:** a lead, its messages, notes and profile are visible only to users whose role and
  teams cover it. A lead a user cannot see answers 404, never 403, so its existence does not leak.

### What comes in

- **Source:** a configured intake endpoint. It has a kind (email, form or call service), an auth
  method, a field mapping and an optional default team.
- **Message:** one delivery from one source, stored raw before anything else runs.
- **Delivery key:** how a repeat delivery is spotted.
  - Email: the source plus the `Message-ID` header. With no `Message-ID`, the SHA-256 of the raw message.
  - Form and call service: the source plus the provider's delivery id header, when the source names one.
    Otherwise the SHA-256 of the body, but only within 10 minutes. The same body after 10 minutes is a
    new message, because a buyer can genuinely send the same form twice.
  - A repeat delivery returns 200 and stores nothing new. Its status is `duplicate_delivery`.
  - **Re-forwards:** a message with a new `Message-ID` whose cleaned body (forward headers stripped)
    matches a message already on the same lead within 24 hours ends as `duplicate_content`. It adds no
    activity and sends no alert.
- **Arrival time:** the moment Firstcall commits the raw message, from the database clock in UTC.
  Every timer starts here.
- **Source time:** the time the message claims for itself: the email `Date` header, or for forwarded
  portal mail the original send time found in the body. It is stored and shown on the lead. It never
  starts a timer, so a wrong sender clock can never make a negative wait.
- **Category:** lead, vendor, support or spam. Every message gets one, forms included.

### Message statuses

- **Working:** `received`, `processing`.
- **Terminal:** `lead_created`, `attached` (added to an existing lead as activity), `not_a_lead`
  (vendor, support or spam, kept in the Not-a-lead tray), `duplicate_delivery`, `duplicate_content`,
  `failed` (with a reason: retries exhausted or unparseable).
- The raw message and its job are written in one transaction, so a message never exists without a job.
- Every message ends in a terminal status with a non-empty reason. Nothing leaves a terminal status,
  except that a monitor can move a `not_a_lead` message into the leads ("rescue"), which records an event.

### The lead

- **Lead:** one person interested in buying, inside one team or the Unassigned pool. The first
  message creates it. Later messages from the same person attach to it as activity.
- **Identity:** the buyer's normalised phone (E.164) and lowercased email. Agent contacts, portal
  relay senders and call-tracking numbers are never identities (see Matching).
- **Compatible name** (amended 2026-11-11, approved by Cristian): a phone match may join a lead
  when both first names are equal, or when the message has no name. A message that has a name
  never joins a nameless lead on phone alone. The first rule, "one side blank", let a household
  member join a lead whose name the rules could not read; the held-out seed-11 run found it.
- **Pool:** the unclaimed leads of one team. **Unassigned** is the pool for leads with no team; the
  monitor watches it.
- **Claim:** a salesperson takes a pool lead. The first claim wins.
- **Real contact:** a logged call, text, email or meeting between the owner and the buyer, in
  either direction. It is logged only by a person: by choosing an action on a typed note, or by
  confirming, with one tap, an action the model found in a note or voice memo. Nothing is logged silently.
- **Touch:** the same as a real contact. Follow-ups count touches.

### Lead states

Open states: `pool`, `owned`, `nurture`. Closed states: `won`, `lost`, `not_real`.
`nurture` is open: the owner keeps the lead on a slow rhythm. `lost` needs a reason and may
name a competitor.

| From | To | Trigger | Who |
|---|---|---|---|
| (none) | pool | A message creates the lead | System |
| pool (Unassigned) | pool (a team) | Place | Monitor, admin |
| pool | owned | Claim | Salesperson or team lead of that team |
| owned | pool | Release | Owner |
| owned | pool | Return to pool | Team lead, admin |
| owned, nurture | pool | Owner left (deactivated or removed from the team) | System |
| owned, nurture | owned, nurture (new owner) | Reassign | Team lead, admin |
| any open | pool (another team) | Move team | Team lead of the current team, admin |
| owned | nurture | Set nurture | Owner, team lead |
| nurture | owned | Resume, or the buyer writes again | Owner, team lead, system |
| owned, nurture | won, lost, not_real | Set outcome | Owner, team lead |
| lost, not_real | owned (same owner, if still active) or pool | The buyer writes again: reopened | System |
| won, lost, not_real | owned or pool | Manual reopen | Team lead, admin |
| won | won | The buyer writes again: activity added, owner alerted (team lead if no owner) | System |

Any transition not in this table is refused. Every transition writes one **lead event**
(see Events) in the same database transaction.

### Speed to lead

- **Time to claim:** first claim minus arrival.
- **Time to first real contact:** the first real contact by any owner minus arrival.
- Both are stored in wall-clock minutes and in business minutes of the lead's team. A lead that
  arrives outside business hours starts counting at the next opening. The dashboard headline is
  business minutes; wall-clock is one click away.
- **Returned to pool or reopened:** the first figures stand. The board shows when the lead re-entered the pool.
- **Placed from Unassigned:** arrival stays the same, so time spent in Unassigned counts.

### Escalation

All times are business minutes of the lead's team, set per team.

| Situation | After (default) | Who is told | How |
|---|---|---|---|
| Unclaimed in a team pool | 15 min | Team leads of that team | Push and email |
| Still unclaimed | 60 min | Admins and the monitor | Push and email |
| In Unassigned | On arrival | The monitor | Push and email |
| Still in Unassigned | 15 min | Admins | Push and email |
| Owned, no real contact | 240 min | Owner, then team leads 60 min later | Push and email |

Each escalation is stored once per lead and level, so a repeated scheduler run cannot send it twice.
There is no automatic release: taking a lead away from a salesperson is a team lead's call.

### Cadence and follow-ups

- **Cadence:** when the owner should next be in touch. It is set per team in config.
- **Default new-lead cadence:** touches due on days 0, 1, 3, 7, 14 and 30 after arrival, then the
  team's steady rhythm (weekly by default). This front-loaded shape is common in published sales
  playbooks; it is a starting point each team can change.
- **Steady rhythms:** daily, weekly or monthly. Nurture leads use monthly.
- **Follow-up:** a task created when the owner is behind the cadence. It never contacts the buyer.
  A lead has at most one open follow-up; a later run updates it instead of adding another.
  Its suggested next step is a rules-based line from the cadence step and the profile gaps (for
  example "No contact in 6 days. Budget not known yet."). No model call.

### Buyer profile

The profile follows BANT, a public sales framework:
- **Budget:** free text, plus an optional range.
- **Authority:** decision maker, shares the decision, not the decision maker, or unknown, plus who else decides.
- **Need:** free text.
- **Timeline:** free text, plus an optional month.
- **Notes:** free text.

The model fills the profile from typed notes and voice memos. A person's edit always wins over the model.

### Outcomes

Won, nurture, lost (reason required, competitor optional) or not real. Outcomes are kept as an
append-only history, so a reopen never erases one.

### Events

- **Lead event:** an append-only row written in the same transaction as the change it records. It has
  an event id (UUIDv7), the org, the lead, a per-lead sequence number, a type and a payload.
- **Types:** `lead.created`, `lead.placed`, `lead.claimed`, `lead.released`, `lead.returned`,
  `lead.reassigned`, `lead.moved_team`, `lead.activity`, `note.added`, `contact.logged`,
  `followup.created`, `followup.done`, `lead.outcome`, `lead.reopened`, `lead.deleted`.
- The lead events table is the outbox: because each event commits with its change, a crash after
  commit loses nothing. PR7 adds delivery on top of it.

### Model modes

- **replay:** answers recorded from live runs. A miss raises an error, so evals and tests fail loudly.
- **record:** live calls whose answers are saved. Refuses any input that is not from the synthetic generator.
- **live:** real calls, under the budget guard.
- **offline:** the running app with no key. See "No model" below.

Every model answer in a committed report comes from a real model on the Firstcall key. There are no
simulated answers in this project.

## Engineering bar (every PR)

- **`./verify.sh`:** ruff, pyright (strict on `firstcall/core/`), pytest with zero skips, per-module
  branch coverage, `alembic upgrade head` on an empty database plus `alembic check` (no drift), the
  replay eval reproducing the committed report, detect-secrets, `uv sync --locked`, `npm ci` and the
  TypeScript build, and `docker compose build`.
- **CI:** a GitHub Actions workflow runs the same script with a Postgres service.
- **Coverage floors:**
  - ≥ 95% branch coverage on the core modules: rules read, match, place, state machine, access, cadence,
    speed-to-lead and escalation, and delivery
  - ≥ 85% on API, intake adapters and the outbound layer
  - checked per module by `scripts/check_coverage.py`
- **Compose healthchecks:**
  - Postgres: `pg_isready`
  - api: `/healthz`
  - worker and scheduler: a heartbeat row less than 60 seconds old
  - Mailpit and, from PR7, the mini CRM: their own health routes
- **Clock:** core code reads time only through an injected clock. A test fails on any direct
  `datetime.now` in `firstcall/core/`.
- **Failpoints:** `FIRSTCALL_FAILPOINT=<name>` makes the process exit with code 137 at that named point. It
  is active only when the variable is set, and the app refuses to start with it when `FIRSTCALL_ENV=beta`.
  Every fault test asserts that the failpoint fired, from the exit code and a log line.
- **Jobs:**
  - A worker takes a job with `FOR UPDATE SKIP LOCKED` and holds a 60-second lease that it renews while working.
  - A reaper returns jobs whose lease ran out, so a dead worker's job is picked up again.
  - After 5 attempts the message ends `failed: retries_exhausted`.
- **Idempotent effects,** each enforced by a unique key, so reprocessing a message gives one lead, one
  set of alerts and one event:
  - a lead is unique on the message that created it
  - an activity is unique on its message
  - an alert is unique on (lead, kind, recipient, cause)
  - a lead event is unique on (lead, cause)
- **Scheduler:** `firstcall tick` runs every 30 seconds in the scheduler container. It runs escalations,
  the reaper and, per team, the nightly follow-up job.

### PR2: users, claim and board

**Builds:**
- users, memberships, roles, sign-in and the access rule
- pools, claim, release, return, reassign, move team, owner left and place-from-Unassigned
- time to claim and escalation
- alerts and the outbound choke point
- the Not-a-lead tray
- browser pages: sign in, the pool, my leads, a lead's detail (read-only plus claim), the monitor's views

**Sign-in.**
- **Passwords:** email and password, hashed with argon2id.
- **Session cookie:** HttpOnly, SameSite=Lax, and Secure outside local.
- **Requests:** a CSRF token on every request that changes state, and 10 sign-in tries a minute per IP and email.
- Admins set passwords. Self-service reset is out of scope.

**Outbound choke point.** One module, `firstcall/outbound/`, is the only code that sends anything. It
checks every recipient against an allowlist: email only to active users of the org, push only to
subscriptions owned by users, and, from PR7, HTTP only to configured endpoints. This is what makes
"never message a buyer" provable. Email has two backends: SMTP (Mailpit locally) and an in-app inbox (the beta).

**Alerts.**
- A new lead alerts every member of its pool, and nobody outside it. New activity on an owned lead alerts only the owner.
- Activity on a pool lead sends no fresh alert.
- **Push** goes out at once for every lead, with a shared notification tag per team, so a device shows one
  notification for a burst instead of a stack.
- **Email** is digested: alerts for the same user and team within 60 seconds go out as one email
  ("3 new leads in North").
- **Push failures:** a 404 or 410 deletes the subscription. A 5xx is retried up to 3 times.
- **Email safety:** text and HTML parts. Buyer text is escaped by the template. Headers never carry buyer
  text beyond a first name with control characters removed.
- **Web push** needs a secure page, a service worker and VAPID keys. Localhost counts as secure for
  local runs; tests use a fake push service.
- On iPhone, web push is understood to work only when the page is added to the Home Screen
  (unverified until tested on a device in PR6). Email is always sent too.

**Acceptance**
- **F19** Sign-in: wrong password, the sign-in rate limit, cookie flags, a missing CSRF token and logout each have a test.
- **F20** An access matrix test runs every lead-scoped route for every role, for a lead in their own
  team and in another team. Allowed pairs succeed. Every other pair gets 404 and leaks no data. Team A
  never sees team B.
- **F21** Twenty concurrent claims give one owner and nineteen "already claimed" (409) answers, over 20
  runs against Postgres.
- **F22** The state machine has a table-driven test: every row in the transition table passes, every
  other pair is refused. Each transition writes one lead event with the next sequence number.
- **F23** Time to claim, with a fake clock: business and wall-clock minutes, an after-hours arrival, a
  weekend and a daylight-saving change, return to pool keeping the first figure, and placement from
  Unassigned keeping the arrival.
- **F24** Escalation: each row of the escalation table fires once at its time and not before, and a
  double tick sends nothing twice (fake clock).
- **F25** Deactivating a user returns their open leads to their pools and alerts those pools (test).
- **F26** Alerts, against the fake push service and Mailpit:
  - pool-only delivery and owner-only activity alerts
  - each push sent at once with the team tag; one digest email for a burst
  - subscription pruning on 404 and 410, and retry on 5xx
  - an email whose buyer text holds `<script>` and a CRLF renders escaped, with clean headers
- **F27** Choke point:
  - A test reads the source tree and fails if any module outside `firstcall/outbound/` imports smtplib, pywebpush or a sending HTTP client.
  - A send to any buyer email or phone is refused, even when it arrives through a note or a crafted user record.
- **F28** On compose, 50 leads on the rules path reach the push request with p95 ≤ 2 s. The figure is
  in the report.
- **F29** Playwright on compose:
  - a salesperson signs in, sees the pool and claims
  - a second browser sees "already claimed"
  - the monitor places a lead from Unassigned and rescues one from the Not-a-lead tray
  - a `<script>` buyer name renders as text

