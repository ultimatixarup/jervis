#!/usr/bin/env bash
# Stop a foreground Jervis started by scripts/start.sh.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

pidfile="$JERVIS_HOME/jervis.pid"
[ -f "$pidfile" ] || die "no pidfile at $pidfile - Jervis does not appear to be running."
pid="$(cat "$pidfile")"
if kill -0 "$pid" 2>/dev/null; then
  info "stopping Jervis (pid $pid)"
  kill "$pid"
else
  warn "pid $pid is not running; removing stale pidfile"
fi
rm -f "$pidfile"
