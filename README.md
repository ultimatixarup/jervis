# Jervis

An always-on, voice-driven personal agent for macOS. Claude is the brain; MCP servers are
the hands. See `PLAN.md` for the full design and phase plan.

Status: **Phase 3**. Jervis listens and talks back.

## Quickstart

```bash
git clone <repo> ~/code/jervis && cd ~/code/jervis
scripts/setup.sh              # Homebrew deps, uv sync, ~/.jervis
scripts/grant-permissions.sh  # macOS privacy panes, one at a time
scripts/doctor.sh             # must be all PASS
```

Put your Anthropic API key in `~/.jervis/.env`, then talk to it:

```bash
scripts/start.sh              # brain + microphone; say "Hey Jarvis, ..."
```

The wake word is **"Hey Jarvis"** until a custom model is trained - see
`scripts/train-wakeword.sh`. Check the audio stack on its own with
`scripts/test-voice.sh`.

Or type instead of talking:

```bash
uv run jervis repl            # conversation; Ctrl-D to leave
uv run jervis ask "what's on my desktop"
uv run jervis status          # what it can reach, and what it last did
uv run jervis audit           # every tool call, most recent last
uv run jervis serve           # the HTTP endpoint on localhost:7777
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

Jervis can also delegate coding work to Claude Code (`mcp/claudecode`). Asking it
questions is read-only; having it change code is `confirm`-tier, is confined to the
project directories listed in `~/.jervis/config.yaml`, and never receives a
permission-bypass flag. See PLAN.md §4b.

Every tool call is appended to `~/.jervis/audit.jsonl` — read it with `jervis audit`.

## Typed endpoint

`jervis serve` exposes `POST /ask`, `POST /confirm` and `GET /health` on
`localhost:7777`. The voice loop is just a client of these, so the brain can be
driven — and tested — without a microphone.

## Layout

```
brain/    Claude tool-use loop, permission guard, memory, HTTP endpoint
voice/    wake word, speech-to-text, text-to-speech, the listening loop
mcp/      one MCP server per capability: macos, claudecode, imessage, mail,
          bank, ubereats
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
