#!/usr/bin/env bash
# Start Jervis in the foreground (development). Logs to ~/.jervis/logs/.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"

mkdir -p "$JERVIS_HOME/logs"

if ! uv run python -c "import jervis_brain.server" >/dev/null 2>&1; then
  die "the brain is not implemented yet (PLAN.md Phase 2). Nothing to start."
fi

die "start.sh is a Phase 2/3 deliverable and is not wired up yet."
