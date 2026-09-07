# Conventions for Claude Code

Read `PLAN.md` first — it is the source of truth. Work one phase at a time. Never skip
acceptance tests.

## Ground rules

- Python 3.12, `uv` workspace. `ruff format` + `ruff check`, `mypy --strict` on `brain/`.
- Every MCP tool declares its tier in its `annotations` under `x-jervis-tier`
  (`read` | `write` | `confirm`). A tool without a tier fails `test_all_tools_have_tier`.
- **Never** pass Claude Code a permission-bypass flag (`--dangerously-skip-permissions`,
  `--permission-mode bypassPermissions`, `--add-dir`). A voice agent must not be able
  to hand it a blank cheque; a test greps `mcp/claudecode` for exactly this.
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

## Two things that will bite you

**Tool names on the wire.** Anthropic tool names must match `^[a-zA-Z0-9_-]{1,128}$`,
so `macos.run_shell` is a 400. The dotted form stays canonical everywhere a human
reads it (config, audit log, tests, e2e scenarios); `mcp_client.to_wire_name` rewrites
it to `macos__run_shell` on the way out and back on the way in. There is a live test
asserting the API still rejects the dotted form.

**MCP sessions and cancel scopes.** `stdio_client` and `ClientSession` are anyio
context managers; anyio requires the task that entered a cancel scope to exit it.
`MCPClientPool` therefore holds every subprocess inside one long-lived supervisor
task. Do not "simplify" that back into an `AsyncExitStack` that `start()` enters and
`stop()` closes — it works until setup and teardown land in different tasks, then
fails with "attempted to exit cancel scope in a different task".

## Test file naming

Test files carry their member as a prefix — `test_macos_safety.py`,
`test_imessage_db.py`, `test_brain_permissions.py`. Two reasons, both load-bearing:

- pytest's default import mode requires unique basenames when test directories have
  no `__init__.py`, and every member's directory is called `tests/`.
- The repo has a top-level `mcp/` directory (PLAN.md §3) with the same name as the
  installed MCP SDK. Under `--import-mode=importlib` pytest synthesizes an `mcp`
  namespace package for it, which then shadows the real SDK and breaks every import.
  Staying on the default import mode with unique basenames avoids that entirely.

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
