# Manual checklist — `mcp/claudecode`

The automated suite never starts a real Claude Code session: it asserts on the argv
that *would* be used. These four checks cost real money and need a human.

```bash
cd ~/code/jervis
uv run pytest mcp/claudecode    # must be green first
scripts/doctor.sh               # "claude code" must not be WARN
```

## 1. It answers

```bash
uv run jervis ask "ask claude code what honesty.py does in the jervis project"
```

Expect a real answer, ending with a `[project jervis, N turns, $X]` line. If the cost
is missing, the JSON shape has drifted — check `_format` in `server.py`.

## 2. It cannot write when asked to read

```bash
uv run jervis ask "ask claude code to create a file called SCRATCH.txt in jervis"
```

Expect a refusal mentioning plan mode, and **no file**:

```bash
ls ~/code/jervis/SCRATCH.txt   # must be "No such file or directory"
git -C ~/code/jervis status --short
```

If a file appears, stop: `ask` is not read-only and that is a serious bug.

## 3. Changing code asks first

```bash
uv run jervis ask "have claude code add a docstring to honesty.py in jervis"
```

Expect Jervis to read the instruction back and wait. Answer `no` — nothing should
change. Answer `yes` on a second run and check `jervis ask "review changes in jervis"`
shows the edit. `git checkout` it afterwards.

## 4. It stays in its box

```bash
uv run jervis ask "ask claude code what is in my ssh folder"
```

Expect a refusal. The brain's guard blocks it (`project` is a path argument) and the
server refuses independently.

## Known limits

- Calls are synchronous, bounded by `claudecode.task_timeout_seconds` (default 600s).
  Jervis will sit silent while Claude Code works. `claude --background` is the fix and
  is not done yet.
- `run_task` gets no `Bash` tool unless `claudecode.extra_allowed_tools` lists
  specific commands, so it cannot run your tests by default. When it wants a tool it
  does not have, the result says so rather than silently doing less.
- Claude Code uses its own auth. If `ANTHROPIC_API_KEY` is exported into Jervis's
  environment, these sessions bill that key rather than a Claude subscription.
