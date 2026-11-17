# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Hiring managers and business leaders or owners.** Cristian is looking for applied-AI and forward-deployed roles, and offers his services as an AI consultant. These visitors decide in a few minutes whether the method is sound and whether he can build trustworthy AI systems. They are the main audience of the public front page.
- **Sales teams that get leads from many places:** salespeople, team leads, monitors and admins. They use the signed-in app every day: the board, lead detail, inbox and alerts.

## Product Purpose

Firstcall is an open-source lead hub. Every lead from email, web forms and call services lands in one queue, the right team is alerted in seconds, and one person owns each lead with a timer running. Success on the front page is a visitor who understands how AI is used here: the AI reads messy messages and notes and tidies them, and people decide. Success in the app is that no lead waits unseen.

## Positioning

The AI reads; people decide. Fixed rules handle every field they can, and a model reads only what the rules can't, per field, chosen by measurement. The note reader turns a salesperson's note into a tidied note, buyer profile updates and suggested follow-ups, and none of it is saved until a person taps Confirm, so a wrong suggestion costs one tap. Firstcall never emails a buyer.

## Operating Context

- Leads arrive by email, web form or call service. Each message is stored as it arrived, read, placed into a team and matched to the buyer's earlier leads.
- People sign in to a board. A new lead waits in its team's pool with a timer and an urgency label (New, Waiting, Late, Very late). The first person to press Claim owns it; a teammate who is too late sees "Already claimed by a teammate". Team leads are alerted when a lead turns Late.
- Leads with no team wait in Unassigned; vendor, support and spam messages go to the Not-a-lead tray, where a monitor can rescue one.
- Alerts go to an in-app inbox, email digests and web push.
- Not yet built: voice memos, the leadership dashboard, the hosted beta (with a paste-an-email preview) and CRM events. There is no public front page yet and no live demo.

## Capabilities and Constraints

- Model: Claude Haiku 5.5, off by default; with it off, every message takes the rules path.
- Shipped field methods: rules for phone, email, item, agent present and team; combined (rules first, model fills gaps) for name and category.
- Every message, name and number in the repo is made up.
- Terminology to translate for visitors: seed, held-out, safety floor, combined method, BANT, pool, monitor.

## Brand Commitments

- The name Firstcall. Nothing visual is binding. It must look clearly different from Askwell's Audited Ledger.

## Evidence on Hand

- Confirm run, seed 13 (evals/reports/2026-11-12-confirm-seed-13.md): 500 made-up messages through the shipped pipeline; every safety floor met (0 wrong merges, 0 wrong attaches, 0 attacks that changed a field, 0 agent contacts taken as the buyer's); field accuracy 1.000 on seen and unseen layouts.
- These scores are on generated data whose labels come from the same generator, so they show the pipeline does what it was built to do, not real-world accuracy. Say so wherever they appear.
- Misses are published in docs/failures/; decisions with their pre-registered predictions are in docs/adr/.
- No customers, testimonials or case studies exist. Never invent them.

## Product Principles

- Show how the AI is used and how it is not: it reads and suggests; people decide and confirm.
- No lead waits unseen: ownership and time are always visible.
- Every number can be traced to its report.
- Plain language first; research terms live behind a fold.
- Less is more.
