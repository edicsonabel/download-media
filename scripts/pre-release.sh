#!/usr/bin/env bash
# Release gate. Run before tagging; every step must pass.
#
#   DM_LIVE_IG_CAROUSEL=<public post> DM_LIVE_BROWSER=brave scripts/pre-release.sh
#
# --no-live skips the real-network checks (what CI runs, since it has neither
# browser cookies nor a stable IP for Instagram). Don't use it to publish.
set -euo pipefail
cd "$(dirname "$0")/.."

live=1
if [[ "${1:-}" == "--no-live" ]]; then
  live=0
elif [[ $# -gt 0 ]]; then
  echo "usage: $0 [--no-live]" >&2
  exit 2
fi

if ((live)); then
  : "${DM_LIVE_IG_CAROUSEL:?set it to a public Instagram post mixing images and videos}"
  : "${DM_LIVE_BROWSER:?set it to a logged-in browser, e.g. brave}"
fi

step() { printf '\n==> %s\n' "$1"; }

step "lint"
.venv/bin/ruff check src tests

step "types"
.venv/bin/mypy

step "tests (es)"
LANG=es_ES.UTF-8 .venv/bin/pytest -q --cov=download_media --cov-fail-under=95

step "tests (en)"
LANG=en_US.UTF-8 .venv/bin/pytest -q

if ((live)); then
  step "live tests"
  .venv/bin/pytest -q -m live tests/live
fi

printf '\nAll checks passed.\n'
