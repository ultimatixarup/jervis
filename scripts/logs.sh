#!/usr/bin/env bash
# Tail the current Jervis log.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
log="${1:-$JERVIS_HOME/logs/jervis.log}"
[ -f "$log" ] || die "no log at $log (is Jervis running?)"
exec tail -f "$log"
