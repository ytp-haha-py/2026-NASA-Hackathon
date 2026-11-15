#!/usr/bin/env bash
# Long-running Phase 2 Goal monitor supervisor.
#
# The Python monitor already reconnects its SSE stream with exponential backoff and
# reconciles every --interval seconds. This wrapper restarts the whole process if it
# exits unexpectedly, and appends logs outside every Goal worktree.
#
#   bash scripts/phase2_monitor.sh
#   PHASE2_NOTIFY_SESSION=ses_... bash scripts/phase2_monitor.sh
#   PHASE2_MONITOR_INTERVAL=60 bash scripts/phase2_monitor.sh
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${PHASE2_STATE_DIR:-$HOME/.local/state/analogx-phase2}"
mkdir -p "$STATE_DIR"
LOG="$STATE_DIR/monitor.log"
INTERVAL="${PHASE2_MONITOR_INTERVAL:-90}"
ARGS=(run --interval "$INTERVAL")
if [ -n "${PHASE2_NOTIFY_SESSION:-}" ]; then
  ARGS+=(--notify-session "$PHASE2_NOTIFY_SESSION")
fi

cd "$ROOT"
while true; do
  printf '[%s] starting phase2 monitor\n' "$(date -Iseconds)" >>"$LOG"
  uv run python -m tools.phase2_monitor.cli "${ARGS[@]}" >>"$LOG" 2>&1
  status=$?
  printf '[%s] monitor exited status=%s; restarting in 5s\n' \
    "$(date -Iseconds)" "$status" >>"$LOG"
  sleep 5
done
