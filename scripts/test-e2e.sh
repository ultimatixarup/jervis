#!/usr/bin/env bash
# Scripted conversations: real MCP servers over stdio, a scripted model.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"
[ -n "$(find tests/e2e/scenarios -name '*.yaml' -print -quit 2>/dev/null)" ] \
  || die "no e2e scenarios in tests/e2e/scenarios."
exec uv run pytest tests/e2e "$@"
