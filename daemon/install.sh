#!/usr/bin/env bash
# Install Jervis as a launchd agent, so it runs at login and restarts if it dies.
#
#   daemon/install.sh              the brain only
#   daemon/install.sh --telegram   the brain and the Telegram bot
#
# Undo with daemon/uninstall.sh. Nothing here needs sudo: these are per-user agents.
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/_common.sh"
cd "$REPO_ROOT"

WITH_TELEGRAM=0
[ "${1:-}" = "--telegram" ] && WITH_TELEGRAM=1

if ! uv run python -c 'import sys; from jervis_brain.credentials import detect; sys.exit(0 if detect().ok else 1)'; then
  die "No Anthropic credentials, so the agent would crash-loop. Fix that first:
scripts/doctor.sh"
fi

if [ "$WITH_TELEGRAM" = "1" ]; then
  uv run python -c '
from jervis_telegram.settings import load, load_env
load_env(); load().require_usable()
' || die "Telegram is not configured. See telegram/TESTING.md."
fi

# A manually started brain already owns port 7777; the agent would fight it.
if pgrep -f "jervis serve" >/dev/null 2>&1; then
  warn "a brain is already running by hand; stopping it so the agent can take over"
  pkill -f "start.sh" 2>/dev/null || true
  pkill -f "jervis-voice" 2>/dev/null || true
  pkill -f "jervis serve" 2>/dev/null || true
  sleep 2
fi

render_and_load() {
  local which="$1" label
  label="$(uv run python -c "
from pathlib import Path
from jervis_brain import daemon
agent = daemon.${which}_agent(Path('$REPO_ROOT'), Path('$JERVIS_HOME'))
daemon.rotate_log(agent.log)
print(daemon.write_plist(agent))
print(agent.label)
" | tail -2)"
  local plist; plist="$(echo "$label" | head -1)"
  label="$(echo "$label" | tail -1)"

  plutil -lint "$plist" >/dev/null || die "generated a malformed plist: $plist"

  # bootout first so re-running picks up a changed plist rather than silently keeping
  # the old one. It fails harmlessly when nothing is loaded.
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$plist" || die "launchctl bootstrap failed for $label"
  launchctl enable "gui/$UID/$label" 2>/dev/null || true
  info "loaded $label"
}

info "installing the brain"
render_and_load brain

if [ "$WITH_TELEGRAM" = "1" ]; then
  info "installing the Telegram bot"
  render_and_load telegram
fi

info "waiting for the brain to answer"
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:7777/health >/dev/null 2>&1; then
    say ""
    info "Jervis is running and will start again at login."
    say "  status:    uv run jervis status"
    say "  logs:      scripts/logs.sh"
    say "  remove:    daemon/uninstall.sh"
    exit 0
  fi
  sleep 1
done

die "the agent loaded but never answered on :7777. Check $JERVIS_HOME/logs/brain.log"
