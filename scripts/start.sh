#!/usr/bin/env bash
# Start Jervis.
#
#   start.sh            typed conversation (the usual way)
#   start.sh --serve    the HTTP brain in the foreground, for another client
#   start.sh --voice    the brain plus the listening loop  (Phase 3, known choppy)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"

MODE="repl"
case "${1:-}" in
  --voice) MODE="voice"; shift ;;
  --serve) MODE="serve"; shift ;;
  --repl)  MODE="repl";  shift ;;
  -h|--help) sed -n '2,6p' "$0" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
esac

if ! uv run python -c 'import sys; from jervis_brain.credentials import detect; sys.exit(0 if detect().ok else 1)'; then
  die "No Anthropic credentials. Put ANTHROPIC_API_KEY in $JERVIS_HOME/.env, or run
\`ant auth login\`. Then check with scripts/doctor.sh."
fi

mkdir -p "$JERVIS_HOME/logs"

# The typed REPL runs the agent in-process, so it needs no brain of its own. Starting
# one anyway would mean two sets of MCP subprocesses and two writers on memory.db.
if [ "$MODE" = "repl" ]; then
  exec uv run jervis repl "$@"
fi

if [ "$MODE" = "serve" ]; then
  exec uv run jervis serve "$@"
fi

# --- voice: a background brain, then the listening loop -----------------------------
BRAIN_LOG="$JERVIS_HOME/logs/brain.log"
PIDFILE="$JERVIS_HOME/jervis.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  die "Jervis already appears to be running (pid $(cat "$PIDFILE")). Run scripts/stop.sh first."
fi

cleanup() {
  trap - INT TERM EXIT
  if [ -n "${BRAIN_PID:-}" ] && kill -0 "$BRAIN_PID" 2>/dev/null; then
    info "stopping the brain (pid $BRAIN_PID)"
    kill "$BRAIN_PID" 2>/dev/null || true
    wait "$BRAIN_PID" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
}
trap cleanup INT TERM EXIT

info "starting the brain; logging to $BRAIN_LOG"
uv run jervis serve >>"$BRAIN_LOG" 2>&1 &
BRAIN_PID=$!
echo "$BRAIN_PID" >"$PIDFILE"

sleep 1
if ! kill -0 "$BRAIN_PID" 2>/dev/null; then
  die "the brain exited immediately. Last lines of $BRAIN_LOG:
$(tail -20 "$BRAIN_LOG")"
fi

info "starting the listening loop"
uv run jervis-voice "$@"
