#!/usr/bin/env bash
# One-time (and safely repeatable) setup for Jervis.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

info "Jervis setup - repo: $REPO_ROOT, state dir: $JERVIS_HOME"

# --- Homebrew -----------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  say "Homebrew is not installed. It is needed for python, portaudio and ffmpeg."
  read -r -p "Install Homebrew now? [y/N] " reply
  case "$reply" in
    [yY]*) /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" ;;
    *) die "Homebrew required. Install it, then re-run this script." ;;
  esac
  # Make brew available in this shell for the rest of the run.
  for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$p" ] && eval "$("$p" shellenv)"
  done
fi

info "Installing Homebrew packages (idempotent)"
for pkg in python@3.12 uv portaudio ffmpeg; do
  if brew list --versions "$pkg" >/dev/null 2>&1; then
    say "  already installed: $pkg"
  else
    brew install "$pkg"
  fi
done

# --- Python workspace ---------------------------------------------------------
info "Syncing the uv workspace"
(cd "$REPO_ROOT" && uv sync)

# Playwright browsers are only needed once mcp/ubereats (Phase 6) is on board.
if (cd "$REPO_ROOT" && uv run python -c "import playwright" >/dev/null 2>&1); then
  info "Installing Playwright Chromium"
  (cd "$REPO_ROOT" && uv run playwright install chromium)
else
  say "  skipping Playwright (not a dependency yet - Phase 6)"
fi

# --- State directory ----------------------------------------------------------
info "Preparing $JERVIS_HOME"
mkdir -p "$JERVIS_HOME"/{logs,chrome-profile,receipts}
chmod 700 "$JERVIS_HOME"

copy_if_absent() {
  local src="$1" dst="$2" mode="${3:-644}"
  if [ -e "$dst" ]; then
    say "  keeping existing $dst"
  else
    cp "$src" "$dst"
    chmod "$mode" "$dst"
    say "  created $dst"
  fi
}
copy_if_absent "$REPO_ROOT/config.example.yaml" "$JERVIS_HOME/config.yaml"
copy_if_absent "$REPO_ROOT/.env.example"        "$JERVIS_HOME/.env" 600

say ""
info "Setup complete. Next:"
say "  scripts/grant-permissions.sh   # macOS privacy permissions, one pane at a time"
say "  scripts/doctor.sh              # must be all PASS"
say ""
say "Put your Anthropic API key in $JERVIS_HOME/.env before running the brain."
