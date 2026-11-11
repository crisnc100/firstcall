# Firstcall

Firstcall is an open-source lead hub for sales teams that get leads from many places. It puts
every lead in one queue, alerts the right team in seconds, and makes one person own each lead
with a timer running.

**Status.** Early work on v0.1. Leads come in by email, web form or call service. Each message
is stored as it arrived, read by fixed rules, placed into a team and matched to the buyer's
earlier leads. There are no users, alerts or model calls yet. Every message, name and number in
this repo is made up.

## Try it

You need [uv](https://docs.astral.sh/uv/), Docker and Node with npm. No API key is needed.

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

## How good are the rules?

`uv run firstcall eval --seed 11` runs 500 made-up messages through the real pipeline and
scores every field against hand-set labels. The latest reports are in
[evals/reports/](evals/reports/). Seed 7 is for tuning, seed 11 is held out, and seed 13 is a
one-time confirm run kept for later. The decision records in [docs/adr/](docs/adr/) state
what we expected before any result existed.

[docs/definitions.md](docs/definitions.md) defines the terms Firstcall uses.

## License

Apache-2.0. See [LICENSE](LICENSE).
