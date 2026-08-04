#!/usr/bin/env bash
# Every test suite in the project.
#
#   ./test.sh
#
# Uses .venv if present, otherwise whatever python3 is on PATH.

set -uo pipefail
cd "$(dirname "$0")"

# Absolute, because the suites run from subdirectories.
ROOT="$PWD"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

GREEN=$'\033[32m'; RED=$'\033[31m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
failed=()

run() {
  local name="$1"; shift
  echo
  echo "${BOLD}▸ ${name}${OFF}"
  if "$@"; then :; else failed+=("$name"); fi
}

run "backend"   bash -c "cd '$ROOT/backend' && '$PY' -m tests.run_all"
run "dashboard" node web/test/render.test.mjs

if [[ -d mobile/node_modules ]]; then
  run "mobile typecheck" bash -c "cd mobile && npx tsc --noEmit && echo '  all files typecheck'"
else
  echo
  echo "${BOLD}▸ mobile typecheck${OFF}"
  echo "  skipped — run 'cd mobile && npm install' first"
fi

echo
echo "============================================================"
if (( ${#failed[@]} )); then
  echo "  ${RED}FAILED:${OFF} ${failed[*]}"
  exit 1
fi
echo "  ${GREEN}All suites passed.${OFF}"
echo "============================================================"
