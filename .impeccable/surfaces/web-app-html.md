---
version: 1
slug: "web-app-html"
primary_target: "web/app.html"
related_targets: ["web/src/styles/app.css","web/src/shell.ts","web/src/leadlist.ts"]
---

# Firstcall signed-in app

## Scope and mode
The signed-in team app (Operate): sign-in, the team pool, My leads, lead detail, Inbox, Alerts on this device, Waiting longest, Unassigned, the Not-a-lead tray and a tray message, Users, Account. A restyle into the On the Rail world: routes, behaviour, copy, roles, labels, keyboard paths and API calls stay as they are.

## Audience, job, action, proof, constraints
- Salespeople, team leads, monitors and admins, all day, on a desktop at a sales desk and on a phone between calls.
- Job: see every waiting lead with its age at a glance, claim one, work it (notes, contacts, profile, outcome), and never leave a lead unseen.
- Primary action: Claim on a pool row; on a lead, the first key in its action column.
- Constraints: dense and calm; age always written in words; nothing scrolls sideways at 320 to 390 px; dark mode keeps working; one token source (rail-tokens.css).

## Direction contract
THESIS: The board is the rail at the pass: every waiting lead is a paper ticket clipped under a steel rail, its age stripe running down the gripped edge. It refuses the generic SaaS board of white cards, blue accent and pill badges.
OWN-WORLD: Brushed steel ground (dark steel at night), thermal paper for every lead, message, note, list and form, near-black ink, square corners, the paper-edge shadow on loose paper only, ink-framed posted cards for action columns and panels. Big Shoulders for the pass (titles, nav, keys, age words, field labels), Martian Mono for what the printer made (timers' companions, times, phone numbers, emails, message text), Atkinson for people (names, prose, forms).
STORY: A salesperson opens the pool, reads each ticket's stripe and age word, presses Claim, sees the ticket leave the rail, opens the lead and works it from its printed facts and slips.
FIRST VIEWPORT: Top bar on steel: wordmark left, uppercase tracked tabs, Inbox key with an ink count and the account key right, a 2px ink rule under it. Under it the page title in Big Shoulders with its count, the team tabs, then the rail bar and the tickets hanging from it, each with an 8px age stripe on its left edge, the timer and age word, the name, the item, team and arrival in print, and Claim at the right.
FORM: On the Rail, inherited from the front page (DESIGN.md); seed:9d37311b472382c0. Extension inside an established world, code-led, no new roll.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Decisions
- 2026-11-18: Cristian approved the left-edge age stripe on lead rows ("Left edge (as now)") over the front page's top band, because it scans down a long list.

## Unresolved
- None.
