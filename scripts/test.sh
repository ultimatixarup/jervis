#!/usr/bin/env bash
# Lint, type-check and test. `--live` also runs tests that hit the real API.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$REPO_ROOT"

LIVE=0
PYTEST_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    *) PYTEST_ARGS+=("$arg") ;;
  esac
done

info "ruff format --check"
uv run ruff format --check .

info "ruff check"
uv run ruff check .

info "mypy --strict brain"
uv run mypy brain

if [ "$LIVE" -eq 1 ]; then
  info "pytest (including live API tests)"
  uv run pytest ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
else
  info "pytest"
  uv run pytest -m "not live" ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
fi

say ""
printf '%sAll green.%s\n' "$C_GRN" "$C_OFF"
