#!/usr/bin/env bash
# Verify every prerequisite and macOS permission. Exit 1 if anything FAILs.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
set +e   # we report failures, we don't abort on them

FAILED=0
ROWS=()

record() { # name status detail
  ROWS+=("$1"$'\t'"$2"$'\t'"$3")
  [ "$2" = "FAIL" ] && FAILED=1
  return 0
}

check() { # name detail-on-pass  (command read from stdin via "$@")
  local name="$1"; shift
  if out="$("$@" 2>&1)"; then
    record "$name" PASS "${out%%$'\n'*}"
  else
    record "$name" FAIL "${out%%$'\n'*}"
  fi
}

# --- tooling ------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  record "uv" PASS "$(uv --version)"
else
  record "uv" FAIL "not on PATH - run scripts/setup.sh"
fi

if pyver="$(cd "$REPO_ROOT" && uv run python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>&1)"; then
  case "$pyver" in
    3.12.*) record "python 3.12" PASS "$pyver (workspace venv)" ;;
    *)      record "python 3.12" FAIL "workspace venv is $pyver" ;;
  esac
else
  record "python 3.12" FAIL "uv run failed: ${pyver%%$'\n'*}"
fi

for bin in ffmpeg sqlite3 osascript; do
  if command -v "$bin" >/dev/null 2>&1; then
    record "$bin" PASS "$(command -v "$bin")"
  else
    record "$bin" FAIL "not on PATH"
  fi
done

if brew list --versions portaudio >/dev/null 2>&1; then
  record "portaudio" PASS "$(brew list --versions portaudio)"
else
  record "portaudio" FAIL "brew install portaudio"
fi

# --- state dir & secrets ------------------------------------------------------
if [ -f "$JERVIS_HOME/config.yaml" ]; then
  record "config.yaml" PASS "$JERVIS_HOME/config.yaml"
else
  record "config.yaml" FAIL "missing - run scripts/setup.sh"
fi

# Any of the SDK's credential sources will do; checking only for a key would make a
# working `ant auth login` profile look broken.
if cred="$(cd "$REPO_ROOT" && uv run python -c '
from jervis_brain.credentials import detect
c = detect()
print(("PASS" if c.ok else "FAIL"), c.source + ": " + c.detail)
' 2>/dev/null)"; then
  record "anthropic credentials" "${cred%% *}" "${cred#* }"
else
  record "anthropic credentials" FAIL "could not check (is the workspace synced?)"
fi

# --- microphone ---------------------------------------------------------------
if system_profiler SPAudioDataType 2>/dev/null | grep -q 'Input Channels'; then
  record "microphone" PASS "input device present"
else
  record "microphone" FAIL "no audio input device found"
fi

# --- Claude Code --------------------------------------------------------------
if command -v claude >/dev/null 2>&1; then
  record "claude code" PASS "$(claude --version 2>&1 | head -1)"
else
  record "claude code" WARN "not on PATH; disable the claudecode server or install it"
fi

# --- macOS privacy permissions ------------------------------------------------
CHAT_DB="$HOME/Library/Messages/chat.db"
if [ ! -f "$CHAT_DB" ]; then
  record "Full Disk Access" WARN "no chat.db on this machine; cannot verify"
elif sqlite3 "file:$CHAT_DB?mode=ro" 'select count(*) from sqlite_master;' >/dev/null 2>&1; then
  record "Full Disk Access" PASS "can read Messages chat.db"
else
  record "Full Disk Access" FAIL "cannot read chat.db - scripts/grant-permissions.sh"
fi

if osascript -e 'tell application "Finder" to return name of home' >/dev/null 2>&1; then
  record "Automation (Finder)" PASS "AppleScript to Finder allowed"
else
  record "Automation (Finder)" FAIL "denied - scripts/grant-permissions.sh"
fi

# Non-destructive Accessibility probe: no keystrokes are sent.
ax="$(osascript -e 'tell application "System Events" to return UI elements enabled' 2>&1)"
case "$ax" in
  true)  record "Accessibility" PASS "UI elements enabled" ;;
  false) record "Accessibility" FAIL "not granted - scripts/grant-permissions.sh" ;;
  *)     record "Accessibility" FAIL "${ax%%$'\n'*}" ;;
esac

shot="$(mktemp -t jervis-doctor).png"
if screencapture -x "$shot" 2>/dev/null && [ -s "$shot" ]; then
  record "Screen Recording" PASS "screencapture works"
else
  record "Screen Recording" FAIL "denied - scripts/grant-permissions.sh (screenshot tool)"
fi
rm -f "$shot"

# --- report -------------------------------------------------------------------
printf '\n%-22s %-6s %s\n' "CHECK" "RESULT" "DETAIL"
printf '%s\n' "----------------------------------------------------------------------"
for row in "${ROWS[@]}"; do
  IFS=$'\t' read -r name status detail <<<"$row"
  case "$status" in
    PASS) colour="$C_GRN" ;;
    WARN) colour="$C_YEL" ;;
    *)    colour="$C_RED" ;;
  esac
  printf '%-22s %s%-6s%s %s\n' "$name" "$colour" "$status" "$C_OFF" "$detail"
done
printf '\n'

if [ "$FAILED" -eq 1 ]; then
  printf '%sSome checks failed.%s\n' "$C_RED" "$C_OFF"
  exit 1
fi
printf '%sAll checks passed.%s\n' "$C_GRN" "$C_OFF"
