# Advisory brief: is Firstcall v0.1 right-sized?

## THE ASK
Cristian wants Firstcall — a clean-room, open-source lead hub (every lead from every source into one queue, timed first-claim ownership, follow-up cadence, outcomes, leadership dashboard, cost/value slider page) — as a portfolio showcase for applied-AI roles plus a usable free beta. He said "focus heavily on packaging this into a reusable product to showcase". He builds with AI builders in small reviewed PRs; he merges each PR himself.

## WHAT WE KNOW
- Sibling projects Ledgerline and Docket each took 4-5 PRs; Ledgerline PR2 alone took ~117 commits across review rounds (Cristian did not think it too big). Review/fix cost per PR is high.
- The first plan was bounced as "not ready" (bounce.md, 27 points). Rev 2 (plan.md) answers all 27: now 6 PRs, ~51 acceptance lines (F1-F51), floors on extraction, category, placement, matching, injection, voice (degraded audio), alert latency; held-out seeds 7/11/13; users/roles/access matrix; outbox webhooks; mini CRM; retention/delete; hosted beta.
- Cristian's binding decisions: report time-to-claim AND time-to-first-contact with escalation; shared demo that resets nightly; signed events + in-repo mini CRM on one contract suite; rules-or-model per field; voice memos in v0.1 (his explicit choice); CRM-agnostic.
- An OpenRouter key now exists ($50 credit limit) for Ledgerline; plan rev 2 asks for a separate Firstcall key with a $10 cap.

## CURRENT DIRECTION
Present rev 2 to Cristian for approval as-is (6 PRs), with its three open decisions (key, transcription fallback = local Whisper, PR6 host = Atlas).

## DECISIONS IN PLAY
1. Is rev 2 over-built for a portfolio v0.1 (51 acceptance lines, 6 PRs)? What would you cut or defer to v0.2 while keeping the showcase (pitch, live walk-through, cost/value sliders, measured numbers) credible?
2. Are any floors unrealistic for synthetic data or likely to cause endless fix rounds (e.g., precision >=0.99 + 0 wrong merges, lead-as-spam <=1%, voice floors on degraded synthetic audio)?
3. Sequencing: is there a smaller first shippable slice that already produces the portfolio artifact (e.g., intake+read+match+eval+cost page) before users/claim/voice?
4. Key: reuse the existing capped key with a per-project budget guard, or a separate key?

## READ FIRST
- /Users/cortega/Documents/Projects/firstcall/firstcall-v01/builds/v01/plan.md
- /Users/cortega/Documents/Projects/firstcall/firstcall-v01/builds/v01/bounce.md
- /Users/cortega/Documents/Projects/ledgerline/ledgerline-v01/builds/v01/plan.md (for sibling scale)
