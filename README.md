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
./verify.sh                 # every check, the same script CI runs
```

[docs/definitions.md](docs/definitions.md) defines the terms Firstcall uses.

## License

Apache-2.0. See [LICENSE](LICENSE).
