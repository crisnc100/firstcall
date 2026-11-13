# Firstcall

Firstcall is an open-source lead hub for sales teams that get leads from many places. It puts
every lead in one queue, alerts the right team in seconds, and makes one person own each lead
with a timer running.

**Status.** Early work on v0.1. Leads come in by email, web form or call service. Each message
is stored as it arrived, read by fixed rules and, when a model is switched on, by Claude Haiku
5.5, then placed into a team and matched to the buyer's earlier leads. People sign in to a
board where salespeople claim leads, and alerts go to an in-app inbox and web push. Every
message, name and number in this repo is made up.

## Try it

You need [uv](https://docs.astral.sh/uv/), Docker and Node with npm. No API key is needed: the
model is off by default (`FIRSTCALL_MODEL_MODE=offline`), so every message takes the rules path.

```sh
./verify.sh                       # every check, the same script CI runs
docker compose up -d --wait       # Postgres, api, worker, scheduler and a mail catcher
uv run firstcall seed             # the made-up demo company from config/demo.yaml
uv run firstcall rotate-secret inbox   # prints a password for the email source, once
```

Then post a raw email to `http://127.0.0.1:8020/intake/inbox` with basic auth (user `inbox`)
and `Content-Type: message/rfc822`. The worker reads it and creates or updates a lead within
a second. Form sources take JSON; [examples/form.html](examples/form.html) is a web form with
a spam trap. A form source's URL ends in a form key. The key is public, since any visitor can
read it in the page, so a form source is protected by its honeypot, its rate limits and the
spam check, not by the key. Sources that must refuse strangers use basic auth or a signature.

To use the board, add yourself as a user and open <http://localhost:8020>:

```sh
uv run firstcall user add you@example.com --name "Your Name" --role admin --role monitor
uv run firstcall user password you@example.com   # asks for a password of 12 or more characters
```

## The board

- **Who signs in.** Salespeople and team leads belong to teams and see their teams' pools
  and their own leads; a team lead sees every lead in their teams. Monitors and admins see
  the whole company, and admins also manage users.
- **Claiming.** A new lead waits in its team's pool. The first person to press Claim gets
  it; a teammate who pressed too late sees "Already claimed by a teammate".
- **Urgency.** Each waiting lead shows a timer and a label: New, Waiting, Late, Very late.
  The timer counts the team's business minutes from when the lead last entered a pool, and
  turns Late when the team leads are alerted. Time to claim still counts from arrival.
- **Unassigned and the Not-a-lead tray.** Leads with no team wait in Unassigned until a
  monitor or admin places them. Vendor, support and spam messages go to the tray, where a
  monitor or admin can rescue one that is really a lead.
- **Alerts.** Alerts arrive as email digests, kept on the in-app Inbox page by default, and
  as web push. An allowlist means Firstcall never emails a buyer, and push goes only to the
  push services on the `FIRSTCALL_PUSH_HOSTS` list.

[docs/board.md](docs/board.md) explains each of these, the settings, and how to run the
pages locally.

## How good are the rules and the model?

`uv run firstcall eval --seed 11` runs 500 made-up messages through the real pipeline and
scores every field against the labels the generator writes along with each message. The latest reports are in
[evals/reports/](evals/reports/). Seed 7 is for tuning, seed 11 is held out, and seed 13 is a
one-time confirm run. Seed 13 refuses to run until `config/field_methods.yaml` is committed.

Each report checks safety floors (no wrong merges, no wrong attaches, no attack sentence
changing a field, no agent's contact taken as the buyer's) and quality floors (field accuracy,
matching recall, team and category). Misses are published in [docs/failures/](docs/failures/), not tuned
away. A rerun gives the same bytes, and `./verify.sh` checks that. The decision records in
[docs/adr/](docs/adr/) state what we expected before any result existed.

`--mode` picks what reads the messages. `rules` (the default) uses no model. The other modes
score three methods per field: rules only, model only, and combined (the rules first, the
model filling only what they missed or were unsure of); seed 13 runs the shipped choice
instead. `replay` takes the model's answers from [evals/recordings/](evals/recordings/), with
no key and no cost. `record` and `live` call the model and spend money, at most `--budget`
dollars per run (no more than $10).
[docs/rerecord.md](docs/rerecord.md) explains when and how to record answers again.

[docs/definitions.md](docs/definitions.md) defines the terms Firstcall uses, including the
intake limits and the read budget.

## License

Apache-2.0. See [LICENSE](LICENSE).
