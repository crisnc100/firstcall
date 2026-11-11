#!/usr/bin/env bash
# The one local and CI gate. Every step must pass, and a skipped test fails pytest.
set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n==> %s\n' "$1"; }

step "uv sync (locked)"
uv sync --locked --quiet

step "ruff check and format"
uv run ruff check .
uv run ruff format --check .

step "pyright (strict on firstcall/core)"
uv run pyright

step "detect-secrets (against .secrets.baseline)"
# Generated synthetic data and its hashes are excluded: they hold random ids, not secrets.
git ls-files -z --cached --others --exclude-standard \
  | grep -zv -E '^(evals/gold/|evals/reports/|uv\.lock$|web/package-lock\.json$)' \
  | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline

step "web: npm ci and the TypeScript build"
(cd web && npm ci --silent && npm run build --silent)

step "docker compose build and up"
docker compose build --quiet
docker compose up -d --wait

step "pytest (zero skips, branch coverage)"
rm -f coverage.json
uv run pytest --cov=firstcall --cov-branch --cov-report=json:coverage.json \
  --cov-report=term:skip-covered

step "per-module branch coverage floors"
uv run python -m scripts.check_coverage coverage.json

printf '\nverify.sh: all checks passed\n'
