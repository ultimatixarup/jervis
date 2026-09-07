#!/usr/bin/env bash
# Walks through the macOS privacy panes Jervis needs, one at a time.
# Nothing here can grant a permission on your behalf - macOS requires you to click.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

pane() { # title  url  instructions...
  local title="$1" url="$2"; shift 2
  say ""
  say "${C_YEL}== $title ==${C_OFF}"
  for line in "$@"; do say "   $line"; done
  say ""
  read -r -p "   Press Enter to open the settings pane (or 's' to skip): " reply
  [ "$reply" = "s" ] && { say "   skipped"; return 0; }
  open "$url"
  read -r -p "   Press Enter once you have granted it: " _
}

say "Jervis needs four macOS permissions. Grant them for your terminal app"
say "(Terminal.app / iTerm / whichever you run scripts/start.sh from)."

pane "Full Disk Access" \
  "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" \
  "Needed to read ~/Library/Messages/chat.db for iMessage." \
  "Click +, add your terminal app, and make sure its switch is ON." \
  "You may need to quit and reopen the terminal afterwards."

pane "Automation" \
  "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation" \
  "Needed for AppleScript control of Finder, Messages, System Events." \
  "Entries appear here only after something asks for them - if the list is" \
  "empty, run scripts/doctor.sh once, approve the prompts, then come back."

pane "Accessibility" \
  "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" \
  "Needed for System Events UI scripting (window and app control)." \
  "Click +, add your terminal app, switch it ON."

pane "Microphone" \
  "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone" \
  "Needed for the wake word and speech-to-text." \
  "Switch your terminal app ON."

say ""
info "Now run: scripts/doctor.sh"
