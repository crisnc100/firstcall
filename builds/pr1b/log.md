# PR1b build log

## Phase 1: deferred fixes, the gateway, the model read (no key)

- **x1, HTML line breaks.** A source line break inside HTML is a space, as a browser shows
  it, and each stripped line is trimmed. A `<br>` at a line end is one line break, so it no
  longer ends a forwarded header block early. A `<br>` on its own line, or a paragraph break,
  is still a blank line. `<pre>` text loses its line breaks too; no lead source uses it.
- **x2, bare CR at intake.** The header budget check turns CRLF, then CR, into LF before it
  looks for the end of the headers. Header sizes are now measured on the normalised bytes.
- **Squash merge and the git-log check.** PR1a was squash-merged, so the gold, the ADRs and
  `read.py` first appear in one commit on main and
  `test_gold_and_predictions_were_committed_before_any_extraction_code` fails there (main's
  CI too). Raised with the orchestrator; not changed here. Every other check passes.
- **Price snapshot.** `config/model_prices.yaml` copies the ADR-002 table, and a test pins
  the two together. The budget pre-check needs a price before any call, so a model with no
  listed price is refused at start in live and record mode.
- **Budget: reserve, then settle.** The pre-check reserves the worst case (prompt at one
  token per three characters, plus a full-length answer) in the ledger under a row lock, and
  settles to the real cost after the call. A crash, or a failed attempt, keeps its
  reservation: the ledger can run high, never low. The day is the UTC date. The cap is per
  org. The default cap is $1.00 a day.
- **Logs.** Every attempt is a `model_calls` row, a failed one with `ok` false and cost 0.
  Replay calls are logged with their recorded tokens, cost and latency, so a replayed eval
  can compute cost per lead from the log. Replay spends nothing from the ledger.
- **Kill switch and key.** `FIRSTCALL_MODEL_OFF=1` and an empty `FIRSTCALL_MODEL_KEY` are
  read at start. Either one means no call and no transport built, so the worker runs on the
  rules path even though no provider is wired. Offline is the default mode.
- **Transport.** One Protocol (`firstcall/model/transport.py`). Only a test fake exists
  (`tests/model_fakes.py`). Live or record mode with a key refuses to start with a plain
  message until phase 2 wires a provider.
- **Recordings.** `evals/recordings/<model>.jsonl` (the folder is a setting), keyed by the
  SHA-256 of model, system prompt, user message and schema, so a changed prompt misses in
  replay and fails loudly.
- **Record mode's synthetic check** compares the SHA-256 of the stored raw message with every
  message and clean twin in `evals/gold/seed-*`.
- **Prompt.** The system prompt is a constant per task. The user message holds the org's item
  and team lists, then the intake text between `<message>` tags; a closing tag inside the
  text is defused.
- **What the model sees.** An email is its From, Reply-To, To, Subject and Date headers plus
  the decoded body text from the rules read, not its MIME source. A form or call post is its
  JSON as sent.
- **Unreadable answers** take the rules path with `needs_reread` and reason `unreadable`.
  The parser falls back to the outermost braces; a value outside its closed list, or of the
  wrong type, is unreadable. A phone or email that does not normalise is read as absent.
- **Methods before the eval.** `config/field_methods.yaml` comes with the seed-11 run
  (phase 2). Until then every field uses the combined method. A rules spam signal holds under
  combined, and a message with one makes no model call, so a spam burst cannot spend the
  budget.
- **Replay miss and non-synthetic input raise** through the job, which retries and then fails
  `retries_exhausted`. They are setup errors, not "model unavailable".
- **Lock.** `process()` locks the message `FOR NO KEY UPDATE`. With `FOR UPDATE`, the
  gateway's log row, written in its own transaction with a foreign key to the message, would
  wait on the processing transaction that is waiting on the gateway.
- **The reread** runs from `tick` when the gateway is available, 50 messages a pass, and
  stops at the first sign the model is unavailable again. It rereads only messages that
  created a lead. It fills empty name, phone, email and item, never a field in
  `leads.edited_fields`, never state or team, and adds no lead event (no event type exists
  for a filled field). A filled phone or email becomes an identity only if no other lead holds
  it. "Not a lead" becomes a `leads.flags` entry for the monitor. A message stops being
  retried after four model calls.
- **Person edits.** Nothing writes `leads.edited_fields` yet; PR2 and PR3 add the screens
  that edit a lead and must append to it.
- **The F16 failpoint** (`during_model_read`) fires after the gateway has logged the answer.
  The test runs real worker processes in replay mode against the test database, not compose
  containers, because the recordings file lives in the test's temporary folder.

**For phase 2.** The eval's clean-twin pass processes a message that is never committed, so
its model call must be logged without a message id (the log row's foreign key would fail).
