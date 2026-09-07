#!/usr/bin/env bash
# Start Jervis in the foreground: the brain, then the listening loop.
# Ctrl-C stops both.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"

mkdir -p "$JERVIS_HOME/logs"
BRAIN_LOG="$JERVIS_HOME/logs/brain.log"
PIDFILE="$JERVIS_HOME/jervis.pid"

if [ ! -f "$JERVIS_HOME/.env" ] || ! grep -qE '^ANTHROPIC_API_KEY=.+' "$JERVIS_HOME/.env"; then
  die "ANTHROPIC_API_KEY is empty in $JERVIS_HOME/.env. Add it, then run scripts/doctor.sh."
fi

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

# Give it a moment to fail loudly rather than hanging the voice loop on a dead brain.
sleep 1
if ! kill -0 "$BRAIN_PID" 2>/dev/null; then
  die "the brain exited immediately. Last lines of $BRAIN_LOG:
$(tail -20 "$BRAIN_LOG")"
fi

info "starting the listening loop"
uv run jervis-voice "$@"
