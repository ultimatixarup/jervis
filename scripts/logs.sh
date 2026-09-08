#!/usr/bin/env bash
# Tail a Jervis log.
#   logs.sh            the brain
#   logs.sh telegram   the Telegram bot
#   logs.sh audit      every tool call, as it happens
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

case "${1:-brain}" in
  brain)    log="$JERVIS_HOME/logs/brain.log" ;;
  telegram) log="$JERVIS_HOME/logs/telegram.log" ;;
  voice)    log="$JERVIS_HOME/logs/voice.log" ;;
  audit)    log="$JERVIS_HOME/audit.jsonl" ;;
  *)        log="$1" ;;
esac

[ -f "$log" ] || die "no log at $log
Is Jervis running? Try: uv run jervis status"
exec tail -f "$log"
