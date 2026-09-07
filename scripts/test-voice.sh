#!/usr/bin/env bash
# 10-second real-microphone smoke test: hear something, print it, speak it back.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"
uv run python -c "import jervis_voice.stt" >/dev/null 2>&1 \
  || die "the voice stack is not implemented yet (PLAN.md Phase 3)."
exec uv run python -m jervis_voice.smoke "$@"
