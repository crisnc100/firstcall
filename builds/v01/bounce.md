**Verdict: not ready to brief a builder.** The plan is a feature list with 12 acceptance lines; both sibling plans fixed definitions, floors and a verify bar before code, and this one has none of the three. The worst holes are identity and tenancy (absent), an eval with no floors, voice with no acceptance line, and a PR2 that is about three PRs.

I read `plan.md` and the Ledgerline and Docket plans. Claims about outside services are marked unverified.

## Settle before PR1 (design holes)

1. **No users, auth, roles or tenancy anywhere.** Claim, owner-only alerts, the monitor and the leadership view all need identity. No test stops team A reading team B's buyer data. "One demo tenant behind an invite code" (`plan.md:162`) means every invitee shares leads, notes and voice recordings.
2. **No lead state machine.**
   - F5 tests "a lead returned to a pool" (`plan.md:140`), but no step defines returning one.
   - Nurture is both a terminal outcome (`:45`) and an ongoing rhythm (`:41`).
   - A won or not-real lead that writes again is undefined, as is a lead whose owner has left.
3. **Claim stops the timer** (`:39-40`), so speed to lead measures a click, not a contact.
   - Claim-and-sit is rewarded: there is no first-touch metric, no auto-release and no escalation of unclaimed leads.
   - There are no business hours, so an 11pm lead claimed at 8am sets the p90.
4. **Match identity is undefined exactly where wrong merges come from.**
   - An agent's phone or email merges all of that agent's clients.
   - Portal relay senders, call-tracking numbers and shared household phones are not addressed.
   - Phone can match lead A while email matches lead B.
   - Two simultaneous messages from a new person create two leads; F4 covers claim races only.
   - A known buyer's "thanks" classed as support or spam is dropped instead of attached, if category gates before match.
5. **"Same message twice" has no key** (`:62`). A content hash drops a buyer's genuine second identical form post; a Message-ID misses re-forwards. Docket fixed this in its Definitions (Docket plan `:29-34`).
6. **No-key behaviour off the recorded path is undefined.** Siblings raise on a replay miss. A new webhook post or a browser-recorded memo (never byte-identical) has no stated result here; Ledgerline specified its equivalent (Ledgerline plan `:289`).
7. **"Source's arrival time" is unspecified.** The email Date header is sender-controlled, forwarded portal mail carries the real time in the body, and clock skew can give negative waits.
8. **The model sits on the alert path with no fallback.** If the budget guard trips or OpenRouter is down, intake behaviour is undefined. "Alerts in seconds" has no latency measurement.

## Proof: the weakest part, and the part a hiring manager reads

9. **No floors on extraction, category or placement.** F2 passes at 40% accuracy. Ledgerline had L11 and Docket had held-out floors.
10. **No held-out split, gold manifest or gold-before-code rule.** Seeded templates are what regex does best, and rules and prompt get tuned on the same 300 messages that are reported. Docket's model lost to rules on category (Docket plan `:189`); this plan has no decision rule for the same result.
11. **ADR predictions land in PR3** (`:124-125`), after the results exist.
12. **F3 is close to unfalsifiable.**
    - Exact-key matching scores about 1.0 unless the generator plants hard negatives such as shared phones and agents.
    - With no recall floor, a matcher that never merges passes.
    - There is no minimum set size.
    - A 0.95 floor allows one wrong merge in 20, which the plan itself calls the worse error.
13. **Asymmetric errors are unmeasured.**
    - A real lead classed as spam is a silently lost lead.
    - A wrong-team placement alerts the wrong pool, where Unassigned would be safe.
    - There are no per-class minimum counts.
    - The generator has no injection cases (`:99-102`), though intake text is attacker-controlled.
14. **Voice has zero acceptance criteria.**
    - The speech-to-text provider is unnamed. Unverified: whether OpenRouter has a transcription route; if not, this needs a second provider, key and budget path.
    - The text-to-speech engine is unnamed (key, determinism, repo size), and clean synthetic audio flatters accuracy.
    - "Note structuring" has no metric for free-text profile fields.
    - "Completed actions count as a touch" (`:77-78`) is the one place model output changes state unreviewed. A hallucinated or future-tense "called" suppresses a follow-up.
15. **Follow-up suggestions are a model call per follow-up per night and are absent from the eval** (`:103-105`). A budget cap hit mid-batch is undefined.
16. **The cost page is not a measurement under simulation.**
    - Tokens come from the stand-in model and prices from the target model.
    - Transcription is priced per minute, not per token.
    - F10 passes when page and report are both wrong.
    - Nothing tests that the "simulated" label reaches the dashboard and cost page.
17. **The replay-day dashboard echoes generator parameters.**
    - Conversion, lost reasons and on-time follow-ups are script inputs.
    - F9 is a tautology if the test reuses the dashboard's query.
    - "Time to log a note" cannot be measured without real users.
    - The demo needs scripted salespeople and an injectable clock; neither is in any PR.

## Failure modes with no criterion

18. **Message lifecycle.** No rule that every message ends in a terminal status (Docket D5), no reaper for a dead worker's job, and nothing saying reprocessing yields one lead, one alert and one CRM event.
19. **Nightly job.** Double runs, missed nights, pile-up on open follow-ups, what counts as a touch, who triggers it, and one config time zone against teams as territories. F6 tests none of these.
20. **Alerts.**
    - Web push needs HTTPS, a service worker and VAPID keys. Unverified: on iPhone it works only when the page is installed to the Home Screen.
    - Burst dedup, expired subscriptions and retries are not mentioned.
    - Alert emails carry buyer text outside F11's browser test.
21. **Outbound webhooks.**
    - User-set URLs open server-side request forgery.
    - No event id or ordering, so backoff can deliver `lead.outcome` before `lead.claimed`.
    - No outbox, so a crash after commit loses the event, and no dead letter.
    - No events for reopen, reassign or new activity.
22. **Intake auth and abuse.**
    - Email webhook auth and provider are unnamed.
    - Most form tools cannot produce a custom HMAC, so the generic adapter may fit no real source.
    - No rate limit: a public endpoint drives model spend and push spam. Docket D12 tested 401, 413 and 429.
23. **F7 cannot prove a negative without one outbound choke point and a recipient allowlist.** Creating a CRM contact can also trigger the customer's own automation to email the buyer, which no test here reaches.
24. **Personal data.** No retention or delete for raw messages, profiles or audio, and no audio format, size or duration limits. Ledgerline had delete, sweep and a notice (Ledgerline plan `:294-298`).
25. **Forms skip the model** (`:64-65`), so form spam bypasses category.
26. **Engineering bar.**
    - No `verify.sh` or CI, zero-skip rule, coverage floors, pyright, secrets scan, migrations, fixed dependency list or compose healthchecks.
    - No failpoint mechanism for F1's "fault test"; Docket asserts the failpoint fired (Docket plan `:117,124`).
    - No acceptance line for alerts, voice, notes, outcomes and reopen, Unassigned, suggestions, replay demo, walk-through or auth.

## CRM: "any CRM" is not shown

27. **The four-job interface has one implementation and a self-written fake.**
    - Webhooks are one-way, so they cannot find a contact or return CRM ids.
    - "Recorded" from what, with no HubSpot account?
    - Outcome-to-stage and user-to-owner mappings are undefined.
    - CRM-side changes never flow back, so the "CRM already" half has two truths about outcome.

## PR split: wrong-sized

- **PR1:** fine, if it owns only the tables it exercises, plus the definitions, gold, predictions and CI scaffold.
- **PR2:** about three PRs — the pool, claim, alerts, board and unlisted auth; notes, voice, follow-up and outcome with their eval; and the CRM and outbound layer, which only consumes events and stands alone. Ledgerline split its upload surface out for this reason (Ledgerline plan `:254`).
- **PR3:** fine once the replay simulator is sized and the predictions move to PR1.
- **PR4:** "same terms as Ledgerline PR5" is wrong. It needs tenancy, an inbound email domain, push keys, a worker and scheduler, audio storage, a transcription key, intake abuse limits and data deletion.

A realistic count is five PRs plus the beta, or cut scope.

## Over-built for a portfolio v0.1

- The HubSpot adapter against a self-authored fake: high effort, low proof.
- Value sliders and the animated walk-through: marketing, with no acceptance line.
- Web push, email and an SMS adapter all at once.
- Three suggestions per follow-up with no eval.

The eval is the under-built part.

## Decisions for Cristian

1. **Beta shape:** a shared canned demo tenant that resets, or an isolated tenant per invite. The second needs a tenant column in PR1.
2. **Timer:** stops at claim, at first touch, or both reported.
3. **HubSpot:** keep it with a real sandbox recording, or replace it with a second in-repo implementation.
4. **Rules beat the model:** what the plan does if that happens.
5. **Clean room:** the day 1/4/7/10/14/21 sequence and the profile fields (timeline, budget, household) read as carried-over domain logic. This is my inference; the plan excludes code and names but says nothing on business logic, and no check enforces it.
