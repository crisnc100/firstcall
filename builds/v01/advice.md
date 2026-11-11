## 1. SCOPE VERDICT

**Over-built** — the core (gold-before-code eval, claim, timers, dashboard, cost page) is right, but about a quarter of the 51 lines serve a second tenant, self-hosters or a CRM that does not exist, and the showcase only appears in the last two PRs.

## 2. ADVISORY

Do not present rev 2 as-is. I could not reach openrouter.ai or pypi.org from this sandbox, so the audio-model list and Piper's licence remain unverified.

**Sequencing**

1. **Split PR1 at the model line.** PR1 (`plan.md:433-441`) is Ledgerline's PR1 and PR2 combined, plus a job system and fault suite. Ledgerline kept its first PR model-free (`ledgerline plan.md:6`).
   - PR1a: definitions, gold, intake, rules read, place, match, rules-only eval. It needs no key.
   - PR1b: model read, per-field choice, floors, measured cost per lead.
   - Risk removed: a single 15-line PR repeating the 117-commit round.
2. **Put the showcase before the extras.** Every PR waits on the one before (`plan.md:36-38`), so the dashboard, cost page and beta sit behind voice and CRM, the two riskiest pieces.
   - Order: 1a, 1b, then users/claim/board, then typed notes/follow-ups/outcomes, then dashboard/demo/cost page, then beta, then voice, then CRM events.
   - This is one or two more PRs, each smaller, and the portfolio artifact is live before the risky work starts.

**Floors (decision 2)**

3. **Several floors are zero-tolerance in disguise.**

   | Floor | What it means at the plan's counts | What a clean pass supports (95%) |
   |---|---|---|
   | Agent contact leak ≤ 1% of ≥ 40 cases | 0 errors | under 8.8% |
   | Match precision ≥ 0.99 on ~60 attaches | 0 wrong | under 6% |
   | Injection 0 of 30 | 0 | under 11% |
   | Voice action precision ≥ 0.97 on ~40 | 1 false positive allowed | — |

   The report prints these intervals (`plan.md:328`), so a reader sees a "≤ 1%" claim next to them. State small-sample floors as counts ("0 of 40"), or raise the counts.
4. **Add a missed-floor rule.** Seed 13 is run once (`plan.md:264-265`), and nothing says what happens if it fails. Any fix after that run makes seed 13 a tuning set. Ledgerline's log predicted its own floor would fail before code existed (`log.md:67`), so this will happen.
5. **The seeds hold out random fills, not layouts.** Seeds 7, 11 and 13 share the same five portal layouts (`plan.md:287`), so rules tuned on 7 score the same on 13.
   - Rev 2 does not fix the bounce's point that seeded templates are what regex does best (`bounce.md:29`).
   - Put two or more portal layouts and one form shape only in seeds 11 and 13, and report seen and unseen layouts apart.
   - Floors apply to seen layouts; unseen is reported with no floor, as Ledgerline did for Spider (`ledgerline plan.md:164`).
   - This is the part an applied-AI reader cares about: where rules break and the model earns its cost.
6. **Simulated floors decide nothing here.** `plan.md:369` lets a Claude stand-in pass floors for the cheapest model. That stand-in would also pick `field_methods.yaml`. Get the key before PR1b.

**Key (decision 4)**

7. **Use a separate key with a $10 cap.** OpenRouter enforces the limit per key, so the cap does not rest on your own guard, and a leak cannot spend Ledgerline's $50. My estimate is well under $1 per 500-message run, so $10 covers the spike, both held-out runs and voice.

**Voice**

8. **Make local Whisper the main transcriber, not the fallback** (`plan.md:841-843`). A Claude stand-in cannot hear audio, so without a key the word error rate and every degraded-audio floor would be invented. Whisper runs offline and costs nothing per minute. Unverified: which Whisper size passes, and its speed on Atlas.
9. **The audio does not fit.** 80 memos × clean and degraded × 2 seeds is 320 clips. At 24 kbps and 25 seconds each that is 24 MB, against a 15 MB cap (`plan.md:315`). Voice has no per-field choice, so one held-out seed is enough: 160 clips, about 12 MB.

**Cuts**

10. **Shrink the CRM PR to signed events plus a receiver.** The mini CRM as an adapter target is the "self-authored fake" the bounce called low proof (`bounce.md:102`). See question 1.
11. **Queue the job in the same transaction as the raw message.** Both are in Postgres, so "kill after raw save, before the job" (`plan.md:476-477`) cannot happen. `reconcile`, `failed: orphaned` and their tests go.
12. **Drop numbers no screen shows.** Re-claim wait, reopen response time and delivery lag (`plan.md:104,166-167`) are not on the dashboard (`plan.md:733-739`).
13. **Delete audio right after transcription everywhere,** as the beta already does (`plan.md:802`). The audio volume and 30-day sweep go. Defer the 90-day raw-body sweep; keep delete-lead.
14. **Keep `org_id`, cut the cross-org test** (F2, second half). No second org exists in v0.1.
15. **Cut the email HMAC verifier** until a provider is picked (`plan.md:444-448`).

**Missing**

16. **Beta abuse.** Any visitor can pick the admin persona (`plan.md:799`). As admin they can add a user with any email address, which then gets alert mail from your domain. They can also reset the shared passwords, or point a webhook at Atlas's internal services.
    - Lock users, sources and endpoints in beta.
    - Send no real mail; show alert emails in an in-app inbox.
    - This also removes both email providers and the SPF/DKIM setup from PR6 (`plan.md:787-788`). A "send a sample lead" button drives the live demo.
17. **Burst collapse contradicts the 2-second floor.** "3 new leads in North" (`plan.md:537`) needs a wait to count, while F25 needs each lead pushed within 2 seconds. Send every push at once with a shared notification tag so the device shows one; digest only the email.

## 3. QUESTIONS

1. **CRM proof:** keep the four-job adapter interface, store adapter and mini CRM as an adapter target, or narrow your earlier decision? *Recommend:* narrow it. Keep the signed, ordered events and make the mini CRM a webhook receiver that builds contacts and deals from them. The adapter interface waits for a real HubSpot sandbox.
2. **Voice before or after the beta is live?** *Recommend:* after. Voice stays in v0.1, but its fix rounds cannot hold up the showcase.
3. **Detected action: log at once with undo, or one tap to confirm?** *Recommend:* one tap. A misheard "I'll call" then cannot silently stop a follow-up, and the 0.97 floor becomes a reported number instead of a gate.
4. **When seed 13 misses a floor:** keep fixing, or publish the miss? *Recommend:* only the safety floors block a merge (wrong merges, injection, agent contact leak). Any other miss is published in `docs/failures/` and becomes a v0.2 item.
