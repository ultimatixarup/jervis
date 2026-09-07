# Manual checklist — `mcp/macos`

Run before tagging `phase-1`. The automated suite covers everything that can be
asserted without watching the screen; these four need a human.

```bash
cd ~/code/jervis
uv run pytest mcp/macos        # must be green first
```

## 1. Register with Claude Desktop

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
(create the file if it does not exist), then quit and reopen Claude Desktop:

```json
{
  "mcpServers": {
    "jervis-macos": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--directory", "/Users/arupbanerjee/code/jervis", "python", "-m", "jervis_mcp_macos"]
    }
  }
}
```

Then ask Claude Desktop: **"list my Downloads folder"** → it should call `list_dir`
and read back real filenames. This is the Phase 1 acceptance criterion.

## 2. Eyeball the side effects

| Ask for | Expect |
|---|---|
| `open_app` with `Safari` | Safari comes to the front |
| `notify` with a title and body | a real notification banner |
| `screenshot` with no region | an image comes back, no shutter sound |
| `set_volume` with `30` | the volume HUD shows 30% |

## 3. Confirm the refusals

None of these should ever run. Each must come back as an error containing
"blocked by policy":

- `read_file` on `~/.ssh/id_rsa`
- `run_shell` with `cat ~/.ssh/id_rsa`
- `list_dir` on `~/Library/Keychains`

## 4. Trash, not `rm`

`move_to_trash` on a throwaway file: the file leaves its folder, appears in the
Trash, and **Put Back** works. If it vanished without reaching the Trash,
something is using `rm` — that is a bug, not a shortcut.

## Known limits

- `toggle_do_not_disturb` needs a Shortcut named "Toggle Do Not Disturb" in the
  Shortcuts app. macOS exposes no scriptable Focus API; the tool says so when the
  shortcut is missing rather than silently doing nothing.
- `system_info`'s `wifi` field needs Location Services on recent macOS. Without it
  the field reads `unavailable (...)` instead of guessing.
- `screenshot` needs Screen Recording. `scripts/doctor.sh` checks it.
