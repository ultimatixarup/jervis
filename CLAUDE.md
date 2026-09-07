# Conventions for Claude Code

Read `PLAN.md` first — it is the source of truth. Work one phase at a time. Never skip
acceptance tests.

## Ground rules

- Python 3.12, `uv` workspace. `ruff format` + `ruff check`, `mypy --strict` on `brain/`.
- Every MCP tool declares its tier in its `annotations` under `x-jervis-tier`
  (`read` | `write` | `confirm`). A tool without a tier fails `test_all_tools_have_tier`.
- **Never** add a tool that moves money, changes security settings, or reads/writes
  `~/.ssh` or the Keychain's contents. Those are `blocked`: the tool must not exist.
  (`security add-generic-password` for Jervis's *own* Plaid token in Phase 5 is the one
  sanctioned Keychain write — it stores our secret, it never reads anyone else's.)
- Servers enforce blocked paths themselves. The brain's permission guard is
  defence-in-depth, not the only line.
- Every tool call appends one line to `~/.jervis/audit.jsonl`. The audit writer must
  never raise — a logging failure must not abort a tool call, and vice versa.
- Before pinning any Anthropic model ID or SDK behaviour, check https://docs.claude.com.
  Current pin: `claude-sonnet-5` in `config.example.yaml`.
- If a macOS permission is missing, don't work around it: add a check to
  `scripts/doctor.sh` and an instruction to `scripts/grant-permissions.sh`.
- Prefer AppleScript / `open` / `mdfind` over raw shell when a native path exists.
- Keep every script idempotent and re-runnable.

## Layout

`uv sync` at the root syncs every workspace member (the root is a *virtual* workspace —
it has no `[project]` table of its own). Members: `brain`, `voice`, `mcp/{macos,imessage,mail,bank,ubereats}`.

Add a phase's third-party dependencies to that member's own `pyproject.toml` when you
start the phase, not before — it keeps `uv sync` fast and Phase 0 installable on a bare
machine.

## Commits

One commit per phase, message `phase N: <summary>`, then `git tag phase-N`. Small
intermediate commits are fine; the tag goes on the green one.

## Running things

```bash
scripts/setup.sh              # once, idempotent
scripts/grant-permissions.sh  # once, interactive
scripts/doctor.sh             # must be all PASS
scripts/test.sh               # ruff + mypy + pytest       (--live adds API contract tests)
```

Scripts for phases that don't exist yet exit non-zero with a message naming the phase.
That is deliberate — a stub must never look like a pass.
