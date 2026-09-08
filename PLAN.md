# Jervis — Personal Voice Agent for macOS

Implementation and testing plan. This document is the source of truth for Claude Code.
Work through the phases in order. Do not start a phase until the previous phase's
acceptance tests pass and are committed.

---

## 0. Goal, non-goals, and safety rules

**Goal.** An always-on, voice-driven personal agent ("Jervis") running on Arup's Mac that
can control the machine, read/send mail and iMessages, order food via Uber Eats, and read
bank balances and transactions, with a Claude model as the brain and MCP servers as the
tool layer. Arup starts everything with one script.

**Non-goals (v1).** iPhone client, multi-user, cloud hosting, autonomous money movement.

**Safety rules (non-negotiable, enforce in code + tests).**

| Tier | Examples | Behaviour |
|------|----------|-----------|
| `read` | list files, read mail, check balance, calendar lookup | Runs silently |
| `write` | create/edit files, send iMessage/mail, open apps, run non-destructive shell | Runs, then Jervis says what it did |
| `confirm` | anything that spends money, deletes files, `rm`, `sudo`, `kill`, sends to a new contact, `git push --force` | Jervis reads back a one-line summary and waits for spoken "yes"/"confirm"; anything else = abort |
| `blocked` | bank transfers/payments, disk formatting, changing security settings, editing `~/.ssh`, reading Keychain | Always refused; tool does not exist in the toolset |

Every tool call is appended to `~/.jervis/audit.jsonl` (timestamp, tool, args, tier,
result summary, confirmation status). Tests must assert this.

---

## 1. Architecture

```
 mic ──► wake word ──► STT ──► Brain (Claude tool-use loop) ──► TTS ──► speaker
                                  │
                                  ├── memory (SQLite: facts, prefs, task history)
                                  ├── permission guard (tier check + audit log)
                                  └── MCP client ──► macos | imessage | mail | bank | ubereats
```

- Everything runs as local processes under one `launchd` agent (`daemon/`).
- Brain talks to MCP servers over stdio. Each server is independently runnable and testable.
- A local HTTP endpoint (`localhost:7777`) accepts typed text so the brain is testable
  without audio and so a phone client can be added later.

---

## 2. Tech stack

- **Language:** Python 3.12 via Homebrew, managed with `uv`. One `pyproject.toml` at root,
  each MCP server is a package under `mcp/` (workspace members).
- **LLM:** Anthropic Python SDK, tool use with streaming. Default model is configurable in
  `config.yaml`; set it to the current Sonnet-class model. Claude Code: check
  https://docs.claude.com/en/docs/about-claude/models for the current model ID before pinning.
- **MCP:** official `mcp` Python SDK (FastMCP-style servers, stdio transport).
- **Wake word:** `openwakeword`. Train a custom "jervis" model; ship a fallback that uses
  the built-in "hey jarvis" model so Phase 3 isn't blocked on training.
- **STT:** `mlx-whisper` (Apple Silicon). Fallback: `faster-whisper`. Use `sounddevice` for
  capture with silence-based end-of-utterance detection (VAD via `webrtcvad` or `silero-vad`).
- **TTS:** macOS `say` as the always-works default (zero deps). Optional: Kokoro (local) or
  ElevenLabs behind a config flag.
- **Memory:** SQLite (`~/.jervis/memory.db`) with tables `facts`, `preferences`, `tasks`,
  `conversations`. Plain FTS5 search; no vector DB in v1.
- **Automation:** `subprocess` for shell, `osascript` for AppleScript/JXA, `open` for apps.
- **iMessage:** read `~/Library/Messages/chat.db` via `sqlite3` (read-only URI); send via
  `osascript` `tell application "Messages"`.
- **Mail:** Gmail via Google API (OAuth, `gmail.readonly` + `gmail.send`). Mail.app
  AppleScript as fallback.
- **Bank:** Plaid (sandbox first, then development). Read-only products: `transactions`,
  `balance`. No `transfer` product ever.
- **Uber Eats:** Playwright (Chromium) against a persistent profile at `~/.jervis/chrome-profile`.
- **Tests:** `pytest`, `pytest-asyncio`, `pytest-mock`, `respx`/`responses` for HTTP,
  `hypothesis` for the permission guard. `ruff` for lint/format, `mypy --strict` for `brain/`.

---

## 3. Repository layout

```
jervis/
  PLAN.md                     this file
  README.md                   one-screen quickstart: setup.sh → start.sh → talk
  CLAUDE.md                   conventions for Claude Code (see §9)
  pyproject.toml              uv workspace root
  config.example.yaml         copied to ~/.jervis/config.yaml by setup.sh
  .env.example                ANTHROPIC_API_KEY, PLAID_*, GOOGLE_*, ELEVENLABS_API_KEY

  brain/
    jervis_brain/
      __init__.py
      agent.py                Claude tool-use loop, streaming, retry
      permissions.py          tier classification, confirm flow, audit log
      memory.py               SQLite memory + FTS
      mcp_client.py           spawns servers from config, aggregates tools, routes calls
      prompts.py              system prompt builder (persona, memory injection, rules)
      server.py               localhost:7777 HTTP endpoint (POST /ask, GET /health)
      cli.py                  `jervis ask "..."` for typed testing
    tests/

  voice/
    jervis_voice/
      wake.py                 openwakeword listener
      stt.py                  mlx-whisper transcription + VAD
      tts.py                  say / kokoro / elevenlabs backends
      loop.py                 wake → record → transcribe → POST /ask → speak
      models/                 jervis.onnx (trained wake word), README on how to retrain
    tests/

  mcp/
    macos/   jervis_mcp_macos/server.py      + tests/
    imessage/ jervis_mcp_imessage/server.py  + tests/
    mail/    jervis_mcp_mail/server.py       + tests/
    bank/    jervis_mcp_bank/server.py       + tests/
    ubereats/ jervis_mcp_ubereats/server.py  + tests/

  daemon/
    com.arup.jervis.plist.template
    install.sh                renders plist, `launchctl bootstrap`
    uninstall.sh

  scripts/
    setup.sh                  Homebrew deps, uv sync, Playwright browsers, ~/.jervis, permissions check
    doctor.sh                 verifies every prerequisite and permission, prints PASS/FAIL table
    start.sh                  starts brain + voice (foreground, logs to ~/.jervis/logs)
    stop.sh
    test.sh                   ruff + mypy + pytest (unit + integration)
    test-e2e.sh               spins brain with mocked MCP servers, runs scripted conversations
    test-voice.sh             10-second mic smoke test: says "Jervis, what time is it"
    logs.sh                   tail -f the current log
    grant-permissions.sh      opens the right System Settings panes, one at a time, with instructions

  tests/
    e2e/                      scripted conversations (YAML) + runner
    fixtures/                 fake chat.db, fake Gmail payloads, Plaid sandbox responses
```

---

## 4. Phases

Each phase ends with: all tests green via `scripts/test.sh`, a commit with the message
format `phase N: <summary>`, and a tag `phase-N`.

### Phase 0 — Scaffold (½ day)

Tasks
- `uv init` workspace, all packages as empty members with `__init__.py`.
- `pyproject.toml` with dev deps, `ruff`, `mypy`, `pytest` config.
- `scripts/setup.sh`: install Homebrew if missing; `brew install python@3.12 uv portaudio ffmpeg`;
  `uv sync`; `uv run playwright install chromium`; `mkdir -p ~/.jervis/{logs,chrome-profile}`;
  copy `config.example.yaml` → `~/.jervis/config.yaml` if absent; copy `.env.example` → `~/.jervis/.env` if absent.
- `scripts/doctor.sh`: checks Python version, uv, ffmpeg, portaudio, `~/.jervis/.env` has
  `ANTHROPIC_API_KEY`, mic device present, Full Disk Access (attempt to `sqlite3` chat.db),
  Automation permission (attempt trivial `osascript`), Accessibility (`osascript` System Events
  keystroke test). Prints a table. Exit 1 if any FAIL.
- `scripts/grant-permissions.sh`: uses `open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"`
  etc. for Full Disk Access, Automation, Accessibility, Microphone. Waits for Enter between each.
- `CLAUDE.md`, `README.md`, `.gitignore` (`.env`, `*.db`, `chrome-profile/`, `models/*.onnx` optional).

Acceptance
- `scripts/setup.sh` runs idempotently twice with no errors on a fresh macOS user account.
- `scripts/doctor.sh` prints a table and correct exit code (test by unsetting API key).
- `scripts/test.sh` runs and passes (trivial tests).

### Phase 1 — `mcp/macos` server (1 day)

Tools (name → tier)
- `run_shell(cmd, cwd?, timeout=30)` → `write`, but `confirm` if cmd matches destructive regex
  (`\brm\b`, `\bsudo\b`, `\bkill\b`, `>\s*/dev/`, `mkfs`, `diskutil`, `chmod\s+-R`, `git\s+push.*--force`).
  Blocked paths: `~/.ssh`, `~/Library/Keychains`, `/System`.
- `run_applescript(script)` → `write`. Same destructive regex applied to the script body.
- `open_app(name)` / `open_url(url)` → `write`.
- `list_dir(path)`, `read_file(path, max_bytes=200k)`, `search_files(query, root)` (uses `mdfind`) → `read`.
- `write_file(path, content, mode=create|overwrite|append)` → `write`; `overwrite` on an existing file → `confirm`.
- `move_to_trash(path)` → `confirm` (uses Finder via AppleScript, never `rm`).
- `notify(title, body)` → `write` (`osascript display notification`).
- `clipboard_get()` → `read`; `clipboard_set(text)` → `write`.
- `screenshot(region?)` → `read`, returns image content block (`screencapture -x`).
- `system_info()` → `read`: battery, volume, current app, Wi-Fi, disk free.
- `set_volume(pct)`, `toggle_do_not_disturb()` → `write`.

Each tool declares its tier in its MCP `annotations` (custom key `x-jervis-tier`). The
brain's permission guard reads this; the server itself also refuses blocked paths so the
guard is defence-in-depth, not the only line.

Tests
- Unit: destructive regex (table-driven, ≥30 cases incl. false negatives like `rmdir`, `alarm`, `format`).
- Unit: blocked-path checker with `~` expansion, symlinks, `..` traversal.
- Integration: spawn the server over stdio with the MCP client; call each `read` tool on a
  temp dir; call `write_file` then `read_file` round-trip; call `move_to_trash` and verify
  file is gone from source (skip on CI without Finder).
- Manual checklist in `mcp/macos/TESTING.md`: `open_app Safari`, `notify`, `screenshot`, `set_volume`.

Acceptance
- Server registered in Claude Desktop config; "list my Downloads folder" works from Claude Desktop.
- `uv run pytest mcp/macos` green.

### Phase 2 — Brain with typed input (1–2 days)

Tasks
- `mcp_client.py`: read `config.yaml` `servers:` list, spawn each over stdio, aggregate tools,
  namespace as `macos.run_shell` etc., map tool call → server.
- `permissions.py`:
  - `classify(tool_name, args) -> Tier` using server annotations + arg inspection.
  - `Guard.execute()` — read/write pass through; `confirm` raises `NeedsConfirmation(summary)`
    which the agent loop turns into a user-facing question and pauses the turn; `blocked` returns
    a tool error the model sees ("This action is blocked by policy").
  - Confirmation words: `yes`, `confirm`, `do it`, `go ahead`; anything else aborts. Confirmation
    expires after 60 s.
  - Audit log writer (append-only JSONL, one line per call, never raises).
- `memory.py`: `remember(kind, text)`, `recall(query, k=8)`, `recent_tasks(n)`. FTS5.
- `prompts.py`: system prompt = persona (British-butler-light, brief, no filler) + rules
  (always narrate write actions, always ask before confirm-tier, never claim an action succeeded
  without a tool result) + top-k memory snippets + current time/date/timezone + frontmost app.
- `agent.py`: streaming tool-use loop, max 12 tool rounds per turn, exponential backoff on
  429/529, per-turn token budget, conversation window of last 20 messages persisted per session.
- `server.py`: `POST /ask {text, session_id}` → `{reply, pending_confirmation?}`; `POST /confirm`;
  `GET /health`. `cli.py`: `jervis ask "..."` and `jervis repl`.

Tests
- Unit: `classify()` table-driven across all macos tools + edge args.
- Property (hypothesis): any shell string containing a destructive token classifies as ≥ `confirm`.
- Unit: audit log line schema (pydantic model), file rotation at 50 MB.
- Unit: memory recall ranks exact matches first; `remember` dedupes identical text.
- Integration: `FakeAnthropic` client that replays scripted tool-use responses; run
  `agent.py` against real `mcp/macos` on a temp dir. Scenarios:
  1. "what's in my Downloads" → one `list_dir` call, reply mentions files.
  2. "delete report.pdf" → `NeedsConfirmation`; reply asks; "yes" → `move_to_trash`; audit shows `confirmed=true`.
  3. "delete report.pdf" → "no" → no tool call; audit shows `confirmed=false`.
  4. Confirmation expiry: "yes" after 61 s is treated as a fresh utterance.
  5. Blocked: "cat ~/.ssh/id_rsa" → tool error surfaced, model told it's blocked, no shell exec.
  6. Model claims success without tool result → post-check strips the claim and appends honest note (test the guard).
- Contract test (network, marked `@pytest.mark.live`, skipped by default): one real call to the
  Anthropic API with a single tool to verify the SDK shape hasn't drifted.

Acceptance
- `jervis repl` → "open Safari and search for uv python" works end to end.
- Scenarios 1–6 green in `scripts/test.sh`.

### Phase 3 — Voice (1–2 days)

Tasks
- `wake.py`: openwakeword streaming from `sounddevice`, threshold configurable, cooldown 2 s,
  plays a short chime on trigger. Ship with built-in "hey jarvis" model; document training a
  "jervis" model in `voice/models/README.md` and add a `scripts/train-wakeword.sh` stub.
- `stt.py`: record until 700 ms of silence (VAD) or 15 s max; transcribe with mlx-whisper
  `small.en` by default; return text + confidence; discard if empty/very low confidence.
- `tts.py`: backend protocol `speak(text) -> None` + `stop()`; `SayBackend` default; Kokoro and
  ElevenLabs behind config. Barge-in: if wake word fires during speech, stop TTS.
- `loop.py`: state machine `IDLE → LISTENING → THINKING → SPEAKING → (AWAITING_CONFIRM) → IDLE`.
  In `AWAITING_CONFIRM`, skip wake word for 60 s and listen directly for the answer.
  Menu-bar status via `rumps` (optional, behind flag) showing state.

Tests
- Unit: state machine transitions (table-driven), confirm-window behaviour, barge-in.
- Unit: VAD end-of-utterance on synthetic audio (speech + silence fixtures generated with `say` → wav).
- Integration: feed a wav fixture through `stt.py`, assert transcript ≈ expected (WER < 0.2).
- Integration: `loop.py` with fake mic (wav injection), fake TTS (records text), live local brain
  with fake Anthropic → assert spoken reply text.
- `scripts/test-voice.sh`: real mic, 10 s, prints what it heard and speaks it back.

Acceptance
- Say "Jervis, what time is it" → spoken answer within 3 s of end of utterance on an M-series Mac.
- Say "Jervis, delete the file test.txt on my Desktop" → asks for confirmation → "yes" → done.

### Phase 4 — iMessage + Mail (1–2 days)

`mcp/imessage` tools
- `list_recent_chats(n=10)`, `read_messages(contact|chat_id, n=20)`, `search_messages(query, n)` → `read`.
  Read via `sqlite3` with `file:...?mode=ro`; decode `attributedBody` blobs (typedstream) when `text` is null.
  Resolve handles → names via Contacts (AppleScript) with an in-memory cache.
- `send_message(to, text)` → `write` if `to` resolves to a contact messaged in the last 90 days,
  else `confirm`.

`mcp/mail` tools (Gmail)
- `search_mail(query, n)`, `read_mail(id)`, `list_unread(n)` → `read`.
- `draft_mail(to, subject, body)` → `write` (creates draft only).
- `send_mail(to, subject, body)` → `confirm` always in v1.
- `archive(id)`, `mark_read(id)` → `write`.
- OAuth flow in `scripts/setup-gmail.sh` (opens browser, stores token in `~/.jervis/gmail-token.json`).

Tests
- Fixture `tests/fixtures/chat.db` built by a script with 3 contacts, 40 messages, some with
  `attributedBody` only. Assert decoding, ordering, search.
- `send_message` tier: recent contact → `write`; unknown → `confirm` (mock Contacts lookup).
- Gmail: `responses`-mocked API for search/read/draft/send; token refresh path; 401 → clear error.
- Integration: brain + imessage server: "what did Kat last text me" → correct tool + reply.
- Manual: `mcp/imessage/TESTING.md` — send yourself a message, read it back.

Acceptance
- "Read my last message from <contact>" and "text <contact> I'm running 10 minutes late" work by voice.
- "Any unread mail from <sender>?" works by voice.

### Phase 5 — Bank (read-only, Plaid) (1 day)

Tools
- `list_accounts()`, `get_balances()`, `get_transactions(account?, days=30, query?)`,
  `spending_summary(days=30)` → all `read`.
- There is no tool that moves money. Add a test that greps the server for the strings
  `transfer`, `payment_initiation`, `/transfer/` and fails if found.

Setup
- `scripts/setup-bank.sh` runs Plaid Link in a local page, exchanges public token, stores
  `access_token` in macOS Keychain via `security add-generic-password` (not in `.env`).
- Sandbox env first (`PLAID_ENV=sandbox`), documented switch to development.

Tests
- Plaid API mocked with sandbox-shaped fixtures; balance aggregation; transaction filtering;
  category summary math.
- Keychain read/write round-trip (marked `@pytest.mark.macos`).
- Negative test: money-movement grep.
- Integration: "how much did I spend on food this month" → `spending_summary` → numeric answer.

Acceptance
- Voice: "what's my checking balance" returns sandbox balance; no write path exists.

### Phase 6 — Uber Eats (1–2 days, flaky by nature)

Tools
- `list_favorites()` → `read`: reads `~/.jervis/ubereats-favorites.yaml` (restaurant, item, options, expected price).
- `preview_order(favorite_name | free_text)` → `read`: Playwright drives the site to the checkout
  page and returns items + total; never clicks Place Order.
- `place_order(preview_id)` → `confirm`: re-validates total within 10% of preview, clicks Place Order,
  captures confirmation screenshot to `~/.jervis/receipts/`.
- `track_order()` → `read`.

Design
- Persistent Chromium profile; `scripts/setup-ubereats.sh` opens the browser for manual login once.
- All selectors in one `selectors.py` with a `scripts/check-selectors.sh` that opens the site and
  verifies each selector still resolves (run this first when things break).
- Every step screenshots into `~/.jervis/logs/ubereats/<run>/` for debugging.

Tests
- Unit: favorites parsing, price-drift check, preview→place token binding (cannot place without a fresh preview).
- Integration: Playwright against a local static HTML mock of search/cart/checkout pages in
  `tests/fixtures/ubereats-mock/`; assert flow reaches "Place Order" only via `place_order`.
- Manual: `mcp/ubereats/TESTING.md` — run `preview_order` for a real favorite; verify total; do NOT place during tests unless you want food.

Acceptance
- Voice: "Jervis, order my usual from <restaurant>" → "That's <items>, $X. Place it?" → "yes" → order placed, receipt saved.

### Phase 7 — Daemon + polish (½ day)  — **done**

Built as two independent agents rather than one: `com.arup.jervis` (the brain) and
`com.arup.jervis.telegram` (the bot, optional). They fail separately and only the
brain is essential.

The plist is generated with `plistlib`, not substituted into a `.plist.template`. A
repo path containing an ampersand or an apostrophe would silently produce malformed
XML under sed, and `plutil -lint` would then reject something nobody had touched.
There is a test that renders into a path containing `R&D <jervis> "quoted"
'apostrophe'` and lints the result.

Two things launchd makes easy to get wrong, both now covered by tests: an agent starts
with no shell profile, so `PATH` must name Homebrew explicitly or `uv` is not found
and the agent crash-loops for no visible reason; and `ThrottleInterval` matters,
because `KeepAlive` plus a config mistake is otherwise a busy loop.

Verified on this machine: `plutil -lint` OK, `launchctl print` shows it running,
`GET /health` 200, and `kill -9` on the brain was recovered from in under a second
with a new pid.

- `daemon/install.sh` renders plist with absolute paths, `KeepAlive`, `RunAtLoad`,
  `StandardOutPath/StandardErrorPath` → `~/.jervis/logs/`, `EnvironmentVariables.PATH` incl. Homebrew.
- `scripts/start.sh` (foreground, for development) and `daemon/install.sh` (background, for daily use).
- Log rotation, `scripts/logs.sh`, `jervis status` CLI.
- README quickstart verified on a fresh macOS user account.

Tests
- `plutil -lint` on rendered plist.
- `launchctl print gui/$UID/com.arup.jervis` shows running after install; `GET /health` 200.
- Kill the brain process → launchd restarts it within 10 s.

---

## 4b. Addendum — `mcp/claudecode` (added after Phase 2)

Not in the original phase plan. Jervis can delegate coding work to the Claude Code
CLI, which is installed on this Mac.

Tools (name → tier)
- `list_code_projects()` → `read`: the projects Claude Code may work in.
- `ask(question, project, session_id?)` → `read`: runs `claude -p --permission-mode plan`
  with `Read`, `Glob` and `Grep` as the only allowed tools. It cannot change anything.
- `run_task(instruction, project, session_id?)` → `confirm`: runs
  `claude -p --permission-mode acceptEdits`. It edits files, so Jervis reads the
  instruction back and waits for a spoken yes.
- `review_changes(project)` → `read`: `git status --short`, so you can hear what changed.

Boundaries, all of which come from `~/.jervis/config.yaml` and none of which any tool
argument can widen:
- `claudecode.projects` lists the only directories Claude Code may run in. A path
  outside them, or inside a blocked root, is refused by the server *and* by the
  brain's guard (`project` is treated as a path argument).
- `--dangerously-skip-permissions`, `bypassPermissions` and `--add-dir` are never
  passed. `test_no_permission_bypass_flag_appears_anywhere_in_the_source` greps the
  server to keep it that way, in the spirit of the Phase 5 money-movement grep.
- `run_task` gets no `Bash` tool by default. Under `acceptEdits` a tool that is not
  auto-approved is *denied* in non-interactive mode rather than prompting, so Claude
  Code can edit files but cannot run commands until specific ones are allow-listed in
  `claudecode.extra_allowed_tools`. `Bash(rm:*)`, `Bash(sudo:*)`, `Bash(git push:*)`,
  `Bash(curl:*)`, `Bash(ssh:*)` and `WebFetch` are denied permanently.
- Every result reports what the session cost and anything Claude Code was refused, so
  a denied tool surfaces instead of looking like a bad answer.

Known limits
- Calls are synchronous and bounded by `task_timeout_seconds` (default 600). A voice
  agent blocking for ten minutes is poor; `claude --background` plus `claude agents`
  would fix it and is the obvious follow-up.
- Claude Code uses its own authentication. If `ANTHROPIC_API_KEY` is set in Jervis's
  environment it will bill that key rather than a Claude subscription.

---

## 4c. Addendum — voice is parked (after Phase 3)

Phase 3 works: the wake word fires at 0.93 from across a room, transcription takes
346ms, and it answered a real question out loud. In daily use it is choppy enough not
to be worth it, so **typed conversation is the front door** and voice sits behind
`scripts/start.sh --voice`. Nothing is deleted and the voice suite still runs.

What was measured before parking it, so it need not be rediscovered:
- wake word: 0.93-0.96 on real speech through speakers, no false fires observed
- transcription: 346ms for a short utterance, but confidence as low as 0.36 on
  "what's the date" - barely over the 0.35 discard threshold
- local pipeline: ~356ms of the 3s budget; Whisper loads once at startup (2.2s)

Leading suspects for the choppiness, in order:
1. **A stale microphone backlog.** `MicSource` queues 200 frames (4s) and drops the
   rest. Nothing drains that queue while the brain is thinking, so the next listen
   starts several seconds behind real time. Draining on re-entry to LISTENING is the
   obvious first fix.
2. **Self-triggering barge-in.** `_speak_and_listen` polls the microphone while `say`
   is talking, and `say` reaches the microphone. A false wake fires STOP_SPEAKING
   mid-sentence, which would read exactly as "choppy".
3. Marginal transcription confidence, which a larger Whisper model would improve at
   some latency cost.

The diagnostic to run first is a real-time factor measurement: read the mic at loop
speed for ten seconds and compare `MicSource.dropped_frames` and audio-seconds against
wall-clock. That was written but never run.

---

## 4d. Addendum — `telegram/` (added after Phase 3)

Not in the original phase plan. PLAN.md §1 said the HTTP endpoint existed "so a phone
client can be added later"; this is that client, and it needed no change to the brain.

A long-polling Telegram bot, a sibling of `voice/`: it reads a message, POSTs `/ask`,
and replies. Confirm-tier actions arrive as inline **Yes / No** buttons; a typed "yes"
works too. One conversation per chat (`telegram:<chat_id>`).

**The allowlist is the security model.** A bot is reachable by anyone who learns its
handle, and Jervis runs shell commands, so `telegram.allowed_user_ids` fails closed:
empty means nobody, and the bot refuses to start rather than answer the world. An
unauthorised message never reaches the brain - there is a test asserting exactly that,
and another asserting the refusal does not describe what Jervis can do.

Raw httpx rather than a bot framework: six API methods, plain JSON, and the repo
already talks HTTP directly. Polling rather than webhooks, because a webhook needs a
public HTTPS endpoint pointed at a laptop behind NAT.

**Known limitation, inherent rather than fixable:** everything said in either
direction passes through Telegram's servers - file contents, message contents, and
bank balances once Phase 5 lands. Worth a deliberate decision about which MCP servers
stay enabled while the bot runs.

---

## 5. Testing strategy summary

| Layer | Tool | Runs in | Command |
|------|------|---------|---------|
| Lint/type | ruff, mypy | always | `scripts/test.sh` |
| Unit | pytest | always | `scripts/test.sh` |
| Integration (MCP over stdio, fake Anthropic) | pytest | always | `scripts/test.sh` |
| E2E scripted conversations | YAML runner in `tests/e2e/` | always | `scripts/test-e2e.sh` |
| Live API contract | pytest `-m live` | on demand | `scripts/test.sh --live` |
| macOS-only (Keychain, Finder, osascript) | pytest `-m macos` | on the Mac | default on macOS, skipped elsewhere |
| Voice smoke | script | on demand | `scripts/test-voice.sh` |
| Manual checklists | `*/TESTING.md` | before each phase tag | — |

E2E YAML format:
```yaml
name: delete requires confirmation
setup: { files: { "~/Desktop/test.txt": "hi" } }
turns:
  - user: "delete test.txt on my desktop"
    expect: { asks_confirmation: true, no_tool_calls: [macos.move_to_trash] }
  - user: "yes"
    expect: { tool_calls: [macos.move_to_trash], file_absent: "~/Desktop/test.txt", audit: { confirmed: true } }
```

Coverage target: ≥85% on `brain/` and `mcp/macos`, ≥70% elsewhere. `permissions.py` must be 100%.

---

## 6. Configuration

`~/.jervis/config.yaml`
```yaml
model: <current Sonnet-class model id>
max_tool_rounds: 12
persona_name: Jervis
voice:
  wake_model: hey_jarvis      # or jervis once trained
  wake_threshold: 0.6
  stt_model: mlx-community/whisper-small.en-mlx
  tts_backend: say            # say | kokoro | elevenlabs
  tts_voice: Daniel
servers:
  macos:    { enabled: true }
  imessage: { enabled: true }
  mail:     { enabled: true }
  bank:     { enabled: false, env: sandbox }
  ubereats: { enabled: false }
permissions:
  confirm_window_seconds: 60
  extra_destructive_patterns: []
```

Secrets live in `~/.jervis/.env` (API keys) or Keychain (bank token). Never in the repo.

---

## 7. Run scripts — what Arup actually types

```bash
git clone <repo> ~/jervis && cd ~/jervis
scripts/setup.sh              # once
scripts/grant-permissions.sh  # once, follows prompts
scripts/doctor.sh             # must be all PASS
scripts/start.sh              # foreground; say "Jervis, ..."
# when happy:
daemon/install.sh             # runs at login forever
```

Optional one-time: `scripts/setup-gmail.sh`, `scripts/setup-bank.sh`, `scripts/setup-ubereats.sh`.

---

## 8. Definition of done (v1)

- Fresh Mac user account → quickstart above → "Jervis, what's on my Desktop" answered by voice in < 15 min of setup.
- All eight acceptance criteria in §4 pass by voice.
- `scripts/test.sh` green, coverage targets met, `phase-7` tagged.
- Audit log shows every tool call from the acceptance run.

---

## 9. CLAUDE.md (conventions for Claude Code — copy into repo)

- Read `PLAN.md` first. Work one phase at a time. Never skip acceptance tests.
- Python 3.12, `uv`, `ruff format`, `mypy --strict` on `brain/`.
- Every tool declares its tier. New tools without a tier fail the `test_all_tools_have_tier` test.
- Never add a tool that moves money, edits security settings, or touches `~/.ssh` / Keychain contents.
- Commit per phase: `phase N: <summary>`, then `git tag phase-N`. Small intermediate commits are fine.
- Before pinning any Anthropic model ID or SDK behaviour, check https://docs.claude.com.
- If a macOS permission is missing, don't work around it — add a check to `doctor.sh` and an instruction to `grant-permissions.sh`.
- Prefer AppleScript/`open`/`mdfind` over shelling into raw commands when a native path exists.
- Keep every script idempotent and re-runnable.
