# Jervis

A personal agent for macOS. You type; it uses your Mac. Claude is the brain; MCP servers are
the hands. See `PLAN.md` for the full design and phase plan.

Status: **Phase 3**. Typed conversation is the way in. Voice exists and works,
but is choppy in practice - see PLAN.md §4c.

## Quickstart

```bash
git clone <repo> ~/code/jervis && cd ~/code/jervis
scripts/setup.sh              # Homebrew deps, uv sync, ~/.jervis
scripts/grant-permissions.sh  # macOS privacy panes, one at a time
scripts/doctor.sh             # must be all PASS
```

Give it Anthropic credentials, either way round:

```bash
ant auth login                        # browser OAuth; nothing to paste, no key on disk
# or put ANTHROPIC_API_KEY in ~/.jervis/.env
```

`scripts/doctor.sh` reports which source it found. Note: an `ant` profile and Claude
Code's own login can conflict - keep one. Then:

```bash
scripts/start.sh              # a prompt; ask it things
```

It shows each tool call as it runs and streams the answer back. `/help` lists the
commands; up-arrow recalls what you typed last time.

```bash
uv run jervis ask "what's on my desktop"    # one question, no prompt
uv run jervis status                        # what it can reach, what it last did
uv run jervis audit                         # every tool call, most recent last
scripts/start.sh --telegram                 # talk to it from Telegram
scripts/start.sh --serve                    # the HTTP endpoint on localhost:7777
scripts/start.sh --voice                    # the microphone (choppy; PLAN.md §4c)
```

## What it will be allowed to do

| Tier | Behaviour |
|------|-----------|
| `read` | runs silently |
| `write` | runs, then Jervis says what it did |
| `confirm` | reads back a one-line summary and waits for a "yes" |
| `blocked` | refused — the tool does not exist in the toolset |

Moving money, formatting disks, changing security settings, and touching `~/.ssh` or the
Keychain are `blocked` by construction, not by prompt.

Jervis can also delegate coding work to Claude Code (`mcp/claudecode`). Asking it
questions is read-only; having it change code is `confirm`-tier, is confined to the
project directories listed in `~/.jervis/config.yaml`, and never receives a
permission-bypass flag. See PLAN.md §4b.

Every tool call is appended to `~/.jervis/audit.jsonl` — read it with `jervis audit`.

## Typed endpoint

`scripts/start.sh --serve` exposes `POST /ask`, `POST /confirm` and `GET /health` on
`localhost:7777`. The voice loop is a client of these; so is anything else you point
at it later.

The typed REPL does *not* go through HTTP — it runs the agent in-process, so it starts
one set of MCP servers and a confirmation stays answerable within the session.

## From your phone

`scripts/start.sh --telegram` puts Jervis behind a Telegram bot, so you can ask it
things from anywhere. Confirm-tier actions arrive as **Yes / No** buttons.

Setup is two steps you have to do yourself — a bot token from @BotFather, and your own
numeric id in `telegram.allowed_user_ids`. `telegram/TESTING.md` walks through both.
The allowlist is the security model: the bot is reachable by anyone who finds its
handle, so an empty list means nobody, and Jervis refuses to start rather than answer
the world.

Note that everything said either way passes through Telegram's servers.

## Traces

Every turn can be traced in OpenTelemetry's GenAI conventions and sent to LangSmith,
which ingests OTLP directly:

```yaml
# ~/.jervis/config.yaml
tracing:
  enabled: true
  project: jervis
  capture_content: false     # see below
```

with `LANGSMITH_API_KEY` in `~/.jervis/.env`. A trace looks like:

```
invoke_agent jervis                 chain   the whole exchange
  chat claude-sonnet-5              llm     tokens in/out, finish reason
  execute_tool macos.list_dir       tool    tier, duration, ok/failed
  chat claude-sonnet-5              llm     the round after the tool results
```

**`capture_content` is off by default and that is deliberate.** Prompts here hold
directory listings and message bodies, tool arguments are usually paths from your home
directory, and Phase 5 adds balances. With it off you still get the shape of every
turn - which tools ran, in what order, how long each took, token usage, tiers,
confirmations, and the text of any error - which is what you actually need to debug.
Turning it on sends the contents to a third party.

Sessions group by conversation, so a Telegram thread and a REPL session appear
separately. `scripts/doctor.sh` shows whether tracing is on and whether content
capture is enabled.

## Layout

```
brain/    Claude tool-use loop, permission guard, memory, HTTP endpoint
voice/    wake word, speech-to-text, text-to-speech, the listening loop
telegram/ the Telegram bot, a client of the HTTP endpoint
mcp/      one MCP server per capability: macos, claudecode, imessage, mail,
          bank, ubereats
daemon/   the launchd agents: install.sh, uninstall.sh
scripts/  setup, doctor, permissions, start/stop, tests
tests/    end-to-end scripted conversations and shared fixtures
```

## Development

```bash
scripts/test.sh          # ruff format+check, mypy --strict brain, pytest
scripts/test.sh --live   # also runs the Anthropic API contract test
```

Conventions for working in this repo: `CLAUDE.md`.
