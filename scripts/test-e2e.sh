#!/usr/bin/env bash
# Scripted conversations against the brain with mocked MCP servers.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"
[ -d tests/e2e ] && [ -n "$(find tests/e2e -name '*.yaml' -print -quit)" ] \
  || die "no e2e scenarios yet (PLAN.md Phase 2)."
exec uv run pytest tests/e2e "$@"
