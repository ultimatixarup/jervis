#!/usr/bin/env bash
# Remove the launchd agents. Jervis stops and does not come back at login.
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/_common.sh"

for label in com.arup.jervis.telegram com.arup.jervis; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  if launchctl print "gui/$UID/$label" >/dev/null 2>&1; then
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    info "unloaded $label"
  fi
  if [ -f "$plist" ]; then
    rm -f "$plist"
    info "removed $plist"
  fi
done

say ""
info "Jervis will no longer start at login. Logs and memory are untouched in $JERVIS_HOME."
