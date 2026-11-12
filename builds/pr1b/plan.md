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

### Model modes

- **replay:** answers recorded from live runs. A miss raises an error, so evals and tests fail loudly.
- **record:** live calls whose answers are saved. Refuses any input that is not from the synthetic generator.
- **live:** real calls, under the budget guard.
- **offline:** the running app with no key. See "No model" below.

Every model answer in a committed report comes from a real model on the Firstcall key. There are no
simulated answers in this project.

## The pipeline

1. **Intake** saves the raw message and its job in one transaction and answers 2xx. If the
   transaction fails, it answers 5xx so the sender retries.
2. **Rules read** pulls phone, email, name, item, agent and form fields with regex and the
   source's field mapping. It also flags spam signals (a filled honeypot field, a sender on the source's spam list).
3. **Model read** (from PR1b) fills what the rules missed, using the per-field method (below). It
   returns a fixed schema. Category and team are enums, so the model cannot invent a value.
4. **Place:** the item or the form's team field maps to a team through config. No match goes to Unassigned.
5. **Match**, before any category gate:
   - Inside the placed team, look for an open or closed lead with the same email, or the same phone
     with a compatible name: first names equal, or the message has no name. A message that has a
     name never joins a nameless lead on phone alone (amended 2026-11-11, see Definitions).
   - With no team, look across the org. Exactly one hit attaches to that lead and its team. More than
     one goes to Unassigned, flagged with the possible matches.
   - **Never an identity:** agent phones and emails (agents are listed per org and detected by the read),
     relay sender addresses listed on the source, and call-tracking numbers listed on the source.
   - **Same phone, different first name** (a shared household phone) does not merge. It creates a new
     lead flagged "possible duplicate".
   - **Phone points to lead A, email to lead B:** no merge. A new lead is flagged "possible duplicate of A and B".
   - Matching and lead creation hold Postgres advisory locks on each identity key, taken in sorted
     order, so two messages from a new person at once make one lead.
6. **Category gate** applies only to unmatched messages. A message from a known buyer attaches whatever
   its category, so a buyer's "thanks" is never dropped. An unmatched vendor, support or spam message
   ends `not_a_lead` in the tray, where the monitor can rescue it.
7. **Create or attach,** write the lead event, and queue the alerts.

### Rules or model, per field

- For each field (phone, email, name, item, agent, category, team), the PR1b eval scores three methods
  on held-out seed 11: rules-only, model-only, and combined (rules first, the model only where rules
  return nothing or are unsure).
- **Decision rule, fixed now:** ship the method with the highest accuracy. When two are within
  1 point, ship the cheaper (rules, then combined, then model). The choice is written to
  `config/field_methods.yaml`, and a test checks that file against the rule applied to the report.
- Because the choice is made on seed 11, the shipped pipeline is confirmed on seed 13. Seed 13 is
  generated and hashed in PR1a and first run only after the choice is committed.

### No model: down, over budget, switched off, or no key

- The model call has an 8-second timeout and one retry.
- When it fails, or the daily budget is hit, or the kill switch is on, or there is no key, the lead is
  still created and alerted from the rules read. The category fails open: anything the rules do not
  call spam is a lead, because a lost lead is the worse error. The message is marked `needs_reread`.
- A reread job runs when the model is back. It only fills empty fields. It never moves an owned lead,
  never closes a lead, and never changes a field a person edited. If it thinks the message is not a
  lead, it flags it for the monitor.
- **With no key:** a new webhook post takes the rules-only path above. A voice memo is still transcribed
  locally and saved as a plain note; its profile and suggested actions say "Needs a model key". Neither
  returns a 500.

### Floors (set now)

Floors apply to seen layouts on the held-out runs at the shipped settings. Where a set is small, the
floor is a count, because a rate like "≤ 1%" on 40 cases means zero errors anyway.

| Area | Measure | Floor | Kind |
|---|---|---|---|
| Matching | Wrong attaches (a message added to the wrong lead), of ≥ 60 repeat messages | 0 | Safety |
| | Wrong merges on hard negatives, of ≥ 100 | 0 | Safety |
| | Recall (repeat messages attached) | ≥ 0.90 | Quality |
| Injection | Attack changes the field to the attacker's value, of ≥ 30 (amended 2026-11-11) | 0 | Safety |
| Extraction | Buyer contact taken from the agent's contact, of ≥ 40 agent cases | 0 | Safety |
| | Phone exact (E.164) | ≥ 0.98 | Quality |
| | Email exact | ≥ 0.99 | Quality |
| | Name (first and last, normalised) | ≥ 0.95 | Quality |
| | Item of interest | ≥ 0.90 | Quality |
| | Agent present (yes or no) | ≥ 0.95 | Quality |
| Category | Overall accuracy | ≥ 0.92 | Quality |
| | Lead classed as not a lead, of ≥ 250 leads | ≤ 2 | Quality |
| | Spam classed as lead | ≤ 10% of spam | Quality |
| Placement | Correct team, when the team is knowable | ≥ 0.90 | Quality |
| | Wrong team (worse than Unassigned) | ≤ 2% | Quality |
| | Ambiguous message placed into a team anyway, of ≥ 30 | ≤ 3 | Quality |
| Notes (text, PR3; degraded audio, PR4) | BANT field filled when it should be, and empty when it should be (F1) | ≥ 0.85 | Quality |
| | Key-term recall in free-text fields | ≥ 0.80 | Quality |
| | Authority and timeline month exact | ≥ 0.85 | Quality |
| | Notes with a number or name not in the source, of ≥ 80 | ≤ 1 | Quality |
| Alerts | Rules path, intake commit to push request, p95 | ≤ 2 s | Quality |

- **Injection floor, amended 2026-11-11 (approved by Cristian):** a case counts only when the
  attacked field takes the attacker's value AND the same message without the attack sentence (its
  clean twin, an additive gold file) gives a different value. The first definition counted any
  output equal to the attacker's value, so an ordinary error that happened to match, such as a
  vendor message classed as a lead, could block a merge with no honest fix. Reports keep the old
  count on a second, labelled line. A case with no twin still counts.
- **Reported, no floor:** completed-action precision and recall (a person confirms every action, so a
  wrong suggestion costs one tap, not a missed follow-up); word error rate on clean and degraded audio;
  every unseen-layout result.

### Missed-floor rule

- **Safety floors block a merge.** Wrong attaches, wrong merges on hard negatives, injection and the
  agent-contact leak must be met on the confirm run, or the PR does not merge.
- **Any other miss is published.** It goes in `docs/failures/` with the numbers and the likely cause,
  and becomes a v0.2 item. The prompts and rules are not tuned against the confirm seed, so it stays a
  fair test.
- The confirm run (seed 13 for messages, seed 11 audio for voice) is run once per PR and committed as run.

### Choosing the model

The same rule as the sibling projects:
- **Candidates:** ADR-002 lists the cheap OpenRouter models that support JSON-schema output, in price
  order, before any result. The list stops at the mini tier. No frontier model is a candidate.
- **One live pass** runs every candidate on seed 7.
- **Gates:** unreadable answers (output that fails the schema even after the parse fallback) ≤ 2%,
  every safety floor met, and model read latency p95 ≤ 6 s.
- **Pick:** among models that pass the gates, the cheapest that is not clearly worse than the best.
  "Clearly worse" means the best model's 95% interval on "message fully right" (every gold field
  correct) sits entirely above this model's score.
- The chosen model then runs seed 11 (for the per-field choice) and seed 13 (the confirm run).

### Key

A separate OpenRouter key for Firstcall with a $10 limit, which OpenRouter enforces per key. Cristian
creates it. PR1a needs no key; PR1b needs it. Every later PR's tests run on replay with no key.

### Predictions before results

ADR skeletons are committed in PR1a, each with a prediction and no result:
- ADR-001 rules or model per field, including how rules fare on unseen layouts
- ADR-002 model candidates and the pick
- ADR-003 matching rules and hard negatives
- ADR-004 lead events as the outbox
- ADR-005 local transcription and the Whisper size
- ADR-006 no-model fallback

Each is closed later with its measured result, whichever way it falls.

### PR1b: model read and cost

**Builds:**
- the model gateway: replay, record, live and offline; a budget pre-check per call; a daily cap kept in the
  budget ledger; a kill switch, `FIRSTCALL_MODEL_OFF=1`; every call logged with model, tokens, cost and
  latency; intake text only in the user message, never the system prompt
- the model read, the no-model fallback and the reread job
- the model pass, the per-field choice, and the confirm run
- measured cost per lead

**Acceptance**
- **F13** The candidate list is in ADR-002 before any result. The pass report shows each candidate's
  gates and "message fully right" score. A test applies the pick rule to the report and gets the chosen
  model. Spend is in the run log, inside the key's $10 limit.
- **F14** The eval reports rules-only, model-only and combined per field, seen and unseen, with
  intervals. `config/field_methods.yaml` matches the decision rule applied to the seed-11 report (test).
- **F15** The seed-13 confirm run is committed. Every safety floor is met. Any other miss is in
  `docs/failures/` under the missed-floor rule. The replay report reproduces, and a replay miss raises.
- **F16** Model down, budget hit, kill switch and no key each create the lead from the rules read, fail
  open on category, and mark `needs_reread`. The reread fills only empty fields and never closes, moves
  or overwrites a person's edit. A worker killed during the model read finishes on retry with one lead
  (failpoint asserted).
- **F17** Record mode refuses non-synthetic input, the budget pre-check stops a call that would pass the
  cap, and the daily cap survives a restart (tests).
- **F18** Cost per lead on seed 11 is in the report, computed from the call log.

