#!/usr/bin/env bash
# The one local and CI gate. Every step must pass, and a skipped test fails pytest.
# With no argument it runs every step in order. CI runs the same steps as parallel jobs, one
# --step at a time (.github/workflows/verify.yml).
set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n==> %s\n' "$1"; }

locked_sync() {
  step "uv sync (locked)"
  uv sync --locked --quiet
}

lint() {
  step "ruff check and format"
  uv run ruff check .
  uv run ruff format --check .

  step "pyright (strict on firstcall/core)"
  uv run pyright
}

secret_scan() {
  step "detect-secrets (against .secrets.baseline)"
  # Generated synthetic data and its hashes are excluded: they hold random ids, not secrets.
  # Recorded model answers are keyed by the SHA-256 of their request.
  git ls-files -z --cached --others --exclude-standard \
    | grep -zv -E '^(evals/gold/|evals/reports/|evals/recordings/|uv\.lock$|web/package-lock\.json$)' \
    | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline
}

web() {
  step "web: npm ci and the TypeScript build"
  (cd web && npm ci --silent && npm run build --silent)
}

browser() {
  step "playwright chromium for the page tests"
  uv run playwright install chromium
}

stack() {
  step "docker compose build and up"
  docker compose build --quiet
  docker compose up -d --wait
}

# Arguments go to pytest: a CI shard passes its paths, its group and where coverage goes.
tests() {
  step "pytest (zero skips, branch coverage)"
  rm -f coverage.json
  uv run pytest --cov=firstcall --cov-branch "$@"
}

floors() {
  step "per-module branch coverage floors"
  uv run python -m scripts.check_coverage "${1:-coverage.json}"
}

replay() {
  step "the rules-only eval reproduces the committed reports"
  uv run python -m scripts.check_replay_report
}

usage() {
  echo "usage: ./verify.sh [--step lint|secrets|web|playwright|stack|eval]" >&2
  echo "       ./verify.sh --step tests [pytest arguments]" >&2
  echo "       ./verify.sh --step floors [coverage.json]" >&2
  exit 2
}

if [[ $# -gt 0 ]]; then
  [[ "$1" == "--step" && $# -ge 2 ]] || usage
  name=$2
  shift 2
  case "$name" in
    tests) tests "$@" ;;
    floors) [[ $# -le 1 ]] || usage; floors "$@" ;;
    *)
      [[ $# -eq 0 ]] || usage
      case "$name" in
        lint) lint ;;
        secrets) secret_scan ;;
        web) web ;;
        playwright) browser ;;
        stack) stack ;;
        eval) replay ;;
        *) usage ;;
      esac
      ;;
  esac
  exit 0
fi

locked_sync
lint
secret_scan
web
browser
stack
tests --cov-report=json:coverage.json --cov-report=term:skip-covered
floors
replay

printf '\nverify.sh: all checks passed\n'
