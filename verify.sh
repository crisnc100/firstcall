#!/usr/bin/env bash
# The one local and CI gate. Every step must pass, and a skipped test fails pytest.
# With no argument it runs every step in order. CI runs the steps as parallel jobs, one --step
# each, and this whole script only in its slow lane (.github/workflows/verify.yml).
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
  # Recorded model answers are keyed by the SHA-256 of their request, and recorded
  # transcripts (the page test's and the voice eval's) by the SHA-256 of their audio, beside
  # the Whisper model's pinned revision.
  git ls-files -z --cached --others --exclude-standard \
    | grep -zv -E '^(evals/gold/|evals/reports/|evals/recordings/|evals/voice/(transcripts/[^/]+\.jsonl|runs/[^/]+\.json|atlas-timing\.json)$|tests/fixtures/voice/page-transcripts\.jsonl$|uv\.lock$|web/package-lock\.json$)' \
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
# With none, it writes the coverage.json that floors reads, as in the full run.
tests() {
  step "pytest (zero skips, branch coverage)"
  # The voice tests run the real ffprobe and ffmpeg; without them they would fail one by one.
  if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
    echo "verify.sh: the voice tests need ffmpeg and ffprobe on PATH" \
      "(brew install ffmpeg, or apt-get install ffmpeg)" >&2
    exit 1
  fi
  rm -f coverage.json
  if [[ $# -eq 0 ]]; then
    set -- --cov-report=json:coverage.json --cov-report=term:skip-covered
  fi
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
tests
floors
replay

printf '\nverify.sh: all checks passed\n'
