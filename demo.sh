#!/usr/bin/env bash
# The judging demo in one command.
#
#   ./demo.sh start    backend (dashboard/feed/serve.py) + dashboard, then a fresh demo state
#   ./demo.sh reset    back to Apr 22, 2026 and $3,140 before the next judge
#   ./demo.sh check    every page and endpoint the demo uses answers; the simulation test passes
#   ./demo.sh stop     stops what start started (a dashboard you started yourself keeps running)
#
# Rules agents by default: free, and the same 9/9 as the evals. The sidebar's Claude switch uses
# ANTHROPIC_API_KEY from .env (metered). The backend runs on the clean demo world; the 20 outside
# PDFs are shown from their recorded run on Live investigation (world/pack/).
set -euo pipefail
cd "$(dirname "$0")"

FEED=http://127.0.0.1:8765
WEB=http://localhost:3000
TMP=${TMPDIR:-/tmp}
PIDS="$TMP/accountableai-demo.pids"

up() { curl -sf -o /dev/null "$1"; }
wait_for() {
  for _ in $(seq 1 60); do up "$1" && return 0; sleep 1; done
  echo "  $2 did not come up; see $3"; exit 1
}

start() {
  [ -x .venv/bin/python ] || { echo "No .venv: python -m venv .venv && .venv/bin/pip install -e '.[dev]'"; exit 1; }
  [ -d dashboard/node_modules ] || (cd dashboard && npm install)
  : > "$PIDS"
  if up "$FEED/api/state"; then echo "  backend already running on :8765"
  else
    (set -a; [ -f .env ] && . ./.env; set +a; nohup .venv/bin/python dashboard/feed/serve.py > "$TMP/accountableai-feed.log" 2>&1 & echo $! >> "$PIDS")
    wait_for "$FEED/api/state" backend "$TMP/accountableai-feed.log"; echo "  backend up on :8765"
  fi
  if up "$WEB"; then echo "  dashboard already running on :3000"
  else
    (cd dashboard && nohup npm run dev > "$TMP/accountableai-web.log" 2>&1 & echo $! >> "$PIDS")
    wait_for "$WEB" dashboard "$TMP/accountableai-web.log"; echo "  dashboard up on :3000"
  fi
  reset
  echo
  echo "  Open $WEB            landing page"
  echo "       $WEB/console    Judge Console: the moment (\$3,140 -> \$2,240)"
  echo "       $WEB/simulation Simulated flow: 15 days, on its own"
  echo "       $WEB/investigation Live investigation: 20 outside PDFs, end to end"
}

reset() {
  curl -sf -X POST "$FEED/api/reset" -o /dev/null && echo "  demo state reset: Apr 22, 2026, \$3,140 unexplained"
}

check() {
  local bad=0
  for p in / /overview /console /simulation /investigation /cases /items /keyway /guardrails /proof; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "$WEB$p")
    printf "  %-16s %s\n" "$p" "$code"; [ "$code" = 200 ] || bad=1
  done
  for p in /api/state /api/simulation /api/presets; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "$FEED$p")
    printf "  %-16s %s\n" "feed$p" "$code"; [ "$code" = 200 ] || bad=1
  done
  for f in simulation.json pack_run.json; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "$WEB/feed/$f")
    printf "  %-16s %s\n" "/feed/$f" "$code"; [ "$code" = 200 ] || bad=1
  done
  held=$(curl -sf "$FEED/api/state" | .venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['headline']['unexplained'])")
  echo "  unexplained now: \$$held (3140.0 right after a reset)"
  ANTHROPIC_API_KEY= .venv/bin/pytest -q -p no:cacheprovider dashboard/feed/tests 2>&1 | tail -1 | sed 's/^/  tests: /'
  [ $bad = 0 ] && echo "  ready" || { echo "  NOT ready: a page or endpoint above did not answer 200"; exit 1; }
}

stop() {
  [ -f "$PIDS" ] && while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDS"
  pkill -f "dashboard/feed/serve.py" 2>/dev/null || true
  for _ in $(seq 1 20); do up "$FEED/api/state" || break; sleep 0.5; done
  rm -f "$PIDS"; echo "  stopped"
}

case "${1:-}" in
  start) start ;; reset) reset ;; check) check ;; stop) stop ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
