#!/usr/bin/env bash
# Stop Jervis, however it was started.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

stopped=0

# Installed as a launchd agent? Boot it out, or it restarts itself immediately.
for label in com.arup.jervis.telegram com.arup.jervis; do
  if launchctl print "gui/$UID/$label" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    info "stopped $label"
    stopped=1
  fi
done

if [ "$stopped" = "1" ]; then
  warn "the agent is still installed and will start again at login."
  warn "to remove it for good: daemon/uninstall.sh"
fi

# Started in the foreground by scripts/start.sh?
pidfile="$JERVIS_HOME/jervis.pid"
if [ -f "$pidfile" ]; then
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    info "stopping the foreground brain (pid $pid)"
    kill "$pid" 2>/dev/null || true
    stopped=1
  fi
  rm -f "$pidfile"
fi

for pattern in "jervis-telegram" "jervis-voice" "jervis serve"; do
  if pgrep -f "$pattern" >/dev/null 2>&1; then
    pkill -f "$pattern" 2>/dev/null || true
    info "stopped $pattern"
    stopped=1
  fi
done

[ "$stopped" = "1" ] || info "nothing was running."
