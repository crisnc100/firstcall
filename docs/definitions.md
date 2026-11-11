# Definitions

These terms are fixed. Code, tests and docs use them with exactly these meanings.

## Who

- **Org:** one company using Firstcall. Every domain table has a non-null `org_id`.
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

## What comes in

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

## Message statuses

- **Working:** `received`, `processing`.
- **Terminal:** `lead_created`, `attached` (added to an existing lead as activity), `not_a_lead`
  (vendor, support or spam, kept in the Not-a-lead tray), `duplicate_delivery`, `duplicate_content`,
  `failed` (with a reason: retries exhausted or unparseable).
- The raw message and its job are written in one transaction, so a message never exists without a job.
- Every message ends in a terminal status with a non-empty reason. Nothing leaves a terminal status,
  except that a monitor can move a `not_a_lead` message into the leads ("rescue"), which records an event.

## The lead

- **Lead:** one person interested in buying, inside one team or the Unassigned pool. The first
  message creates it. Later messages from the same person attach to it as activity.
- **Identity:** the buyer's normalised phone (E.164) and lowercased email. Agent contacts, portal
  relay senders and call-tracking numbers are never identities.
- **Compatible name:** a phone match may join a lead when both first names are equal, or when
  the message has no name. A message that has a name never joins a nameless lead on phone
  alone. Rules leave a lead nameless when they cannot find the name, so a nameless lead says
  nothing about who it is.
- **Pool:** the unclaimed leads of one team. **Unassigned** is the pool for leads with no team; the
  monitor watches it.
- **Claim:** a salesperson takes a pool lead. The first claim wins.
- **Real contact:** a logged call, text, email or meeting between the owner and the buyer, in
  either direction. It is logged only by a person: by choosing an action on a typed note, or by
  confirming, with one tap, an action the model found in a note or voice memo. Nothing is logged silently.
- **Touch:** the same as a real contact. Follow-ups count touches.

## Lead states

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

## Speed to lead

- **Time to claim:** first claim minus arrival.
- **Time to first real contact:** the first real contact by any owner minus arrival.
- Both are stored in wall-clock minutes and in business minutes of the lead's team. A lead that
  arrives outside business hours starts counting at the next opening. The dashboard headline is
  business minutes; wall-clock is one click away.
- **Returned to pool or reopened:** the first figures stand. The board shows when the lead re-entered the pool.
- **Placed from Unassigned:** arrival stays the same, so time spent in Unassigned counts.

## Escalation

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

## Cadence and follow-ups

- **Cadence:** when the owner should next be in touch. It is set per team in config.
- **Default new-lead cadence:** touches due on days 0, 1, 3, 7, 14 and 30 after arrival, then the
  team's steady rhythm (weekly by default). This front-loaded shape is common in published sales
  playbooks; it is a starting point each team can change.
- **Steady rhythms:** daily, weekly or monthly. Nurture leads use monthly.
- **Follow-up:** a task created when the owner is behind the cadence. It never contacts the buyer.
  A lead has at most one open follow-up; a later run updates it instead of adding another.
  Its suggested next step is a rules-based line from the cadence step and the profile gaps (for
  example "No contact in 6 days. Budget not known yet."). No model call.

## Buyer profile

The profile follows BANT, a public sales framework:
- **Budget:** free text, plus an optional range.
- **Authority:** decision maker, shares the decision, not the decision maker, or unknown, plus who else decides.
- **Need:** free text.
- **Timeline:** free text, plus an optional month.
- **Notes:** free text.

The model fills the profile from typed notes and voice memos. A person's edit always wins over the model.

## Outcomes

Won, nurture, lost (reason required, competitor optional) or not real. Outcomes are kept as an
append-only history, so a reopen never erases one.

## Events

- **Lead event:** an append-only row written in the same transaction as the change it records. It has
  an event id (UUIDv7), the org, the lead, a per-lead sequence number, a type and a payload.
- **Types:** `lead.created`, `lead.placed`, `lead.claimed`, `lead.released`, `lead.returned`,
  `lead.reassigned`, `lead.moved_team`, `lead.activity`, `note.added`, `contact.logged`,
  `followup.created`, `followup.done`, `lead.outcome`, `lead.reopened`, `lead.deleted`.
- The lead events table is the outbox: because each event commits with its change, a crash after
  commit loses nothing. Webhook delivery reads from it.

## Model modes

- **replay:** answers recorded from live runs. A miss raises an error, so evals and tests fail loudly.
- **record:** live calls whose answers are saved. Refuses any input that is not from the synthetic generator.
- **live:** real calls, under the budget guard.
- **offline:** the running app with no key. Leads come from the rules read alone and are
  marked `needs_reread`.

Every model answer in a committed report comes from a real model on the Firstcall key. There are no
simulated answers in this project.
