# Jervis

An always-on, voice-driven personal agent for macOS. Claude is the brain; MCP servers are
the hands. See `PLAN.md` for the full design and phase plan.

Status: **Phase 0 (scaffold)**. Nothing talks yet.

## Quickstart

```bash
git clone <repo> ~/code/jervis && cd ~/code/jervis
scripts/setup.sh              # Homebrew deps, uv sync, ~/.jervis
scripts/grant-permissions.sh  # macOS privacy panes, one at a time
scripts/doctor.sh             # must be all PASS
```

Put your Anthropic API key in `~/.jervis/.env`, then (from Phase 2 on):

```bash
scripts/start.sh              # foreground; say "Jervis, ..."
```

When you're happy with it, `daemon/install.sh` (Phase 7) runs it at login forever.

## What it will be allowed to do

| Tier | Behaviour |
|------|-----------|
| `read` | runs silently |
| `write` | runs, then Jervis says what it did |
| `confirm` | reads back a one-line summary and waits for a spoken "yes" |
| `blocked` | refused — the tool does not exist in the toolset |

Moving money, formatting disks, changing security settings, and touching `~/.ssh` or the
Keychain are `blocked` by construction, not by prompt.

Every tool call is appended to `~/.jervis/audit.jsonl`.

## Layout

```
brain/    Claude tool-use loop, permission guard, memory, HTTP endpoint
voice/    wake word, speech-to-text, text-to-speech, the listening loop
mcp/      one MCP server per capability: macos, imessage, mail, bank, ubereats
daemon/   launchd agent (Phase 7)
scripts/  setup, doctor, permissions, start/stop, tests
tests/    end-to-end scripted conversations and shared fixtures
```

## Development

```bash
scripts/test.sh          # ruff format+check, mypy --strict brain, pytest
scripts/test.sh --live   # also runs the Anthropic API contract test
```

Conventions for working in this repo: `CLAUDE.md`.
