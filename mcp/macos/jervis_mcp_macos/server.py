"""MCP server: controlled access to this Mac.

Every tool declares a tier in its ``meta`` (see ``tiers.py``). Blocked paths are refused
here, in the server, regardless of what the brain's guard decides.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .safety import (
    assert_command_allowed,
    assert_path_allowed,
    is_blocked_path,
)
from .tiers import Escalation, Tier, tool_meta

READ_ONLY = ToolAnnotations(read_only_hint=True)

mcp = MCPServer(
    "jervis-macos",
    version=__version__,
    instructions=(
        "Controls Arup's Mac: files, apps, shell, clipboard, screenshots, notifications. "
        "Paths under ~/.ssh, ~/Library/Keychains and /System are refused outright."
    ),
)


class ToolFailure(ToolError):
    """A tool could not do what was asked. The message is shown to the model."""


def _run(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: float = 30,
    stdin: str | None = None,
) -> str:
    """Run a subprocess and return stdout, raising ToolFailure on non-zero exit."""
    try:
        # argv is built by callers; it is never handed to a shell.
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolFailure(f"{argv[0]} is not available on this Mac") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolFailure(f"{argv[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise ToolFailure(detail)
    return proc.stdout


def _osascript(script: str, *, language: str = "AppleScript", timeout: float = 30) -> str:
    return _run(["osascript", "-l", language, "-"], stdin=script, timeout=timeout).strip()


# --------------------------------------------------------------------------- shell


@mcp.tool(
    meta=tool_meta(Tier.WRITE, Escalation.DESTRUCTIVE_COMMAND),
    description=(
        "Run a shell command. Destructive commands (rm, sudo, kill, diskutil, "
        "force-push, ...) require spoken confirmation first. Prefer a purpose-built "
        "tool when one exists - open_app, write_file, move_to_trash, search_files."
    ),
)
def run_shell(cmd: str, cwd: str | None = None, timeout: int = 30) -> str:
    """Run *cmd* with /bin/zsh and return its combined output."""
    assert_command_allowed(cmd, cwd)
    working_dir = assert_path_allowed(cwd) if cwd else None
    if working_dir is not None and not working_dir.is_dir():
        raise ToolFailure(f"cwd does not exist: {cwd}")

    try:
        # Running an arbitrary shell command IS this tool's job; the guard rails
        # are the tier and the blocked-path scan above, not argv construction.
        proc = subprocess.run(
            ["/bin/zsh", "-c", cmd],
            cwd=str(working_dir) if working_dir else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolFailure(f"command timed out after {timeout}s") from exc

    parts = [proc.stdout.rstrip(), proc.stderr.rstrip()]
    output = "\n".join(p for p in parts if p)
    if proc.returncode != 0:
        return f"exit {proc.returncode}\n{output}".rstrip()
    return output or "(no output)"


@mcp.tool(
    meta=tool_meta(Tier.WRITE, Escalation.DESTRUCTIVE_COMMAND),
    description="Run an AppleScript and return its result.",
)
def run_applescript(script: str, timeout: int = 30) -> str:
    """Run *script* with osascript."""
    assert_command_allowed(script)
    return _osascript(script, timeout=timeout) or "(no result)"


# --------------------------------------------------------------------------- apps


@mcp.tool(meta=tool_meta(Tier.WRITE), description="Open a Mac application by name.")
def open_app(name: str) -> str:
    _run(["open", "-a", name])
    return f"Opened {name}."


@mcp.tool(meta=tool_meta(Tier.WRITE), description="Open a URL in the default browser.")
def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ToolFailure("only http:// and https:// URLs can be opened")
    _run(["open", url])
    return f"Opened {url}."


# --------------------------------------------------------------------------- files


@mcp.tool(
    meta=tool_meta(Tier.READ),
    annotations=READ_ONLY,
    description="List the contents of a directory.",
)
def list_dir(path: str = "~") -> str:
    target = assert_path_allowed(path)
    if not target.is_dir():
        raise ToolFailure(f"not a directory: {path}")
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    if not entries:
        return f"{target} is empty."
    lines = [f"{target} ({len(entries)} items)"]
    for entry in entries:
        try:
            size = "" if entry.is_dir() else f"  {entry.stat().st_size:,} bytes"
        except OSError:
            size = "  (unreadable)"
        lines.append(f"  {entry.name}{'/' if entry.is_dir() else ''}{size}")
    return "\n".join(lines)


@mcp.tool(
    meta=tool_meta(Tier.READ),
    annotations=READ_ONLY,
    description="Read a text file. Truncates at max_bytes.",
)
def read_file(path: str, max_bytes: int = 200_000) -> str:
    target = assert_path_allowed(path)
    if not target.is_file():
        raise ToolFailure(f"not a file: {path}")
    data = target.read_bytes()[: max_bytes + 1]
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return text + (f"\n\n[truncated at {max_bytes:,} bytes]" if truncated else "")


@mcp.tool(
    meta=tool_meta(Tier.READ),
    annotations=READ_ONLY,
    description="Search for files by name or content using Spotlight (mdfind).",
)
def search_files(query: str, root: str | None = None, limit: int = 40) -> str:
    argv = ["mdfind"]
    if root:
        argv += ["-onlyin", str(assert_path_allowed(root))]
    argv.append(query)
    hits = [line for line in _run(argv).splitlines() if line.strip()]
    kept = [h for h in hits if not is_blocked_path(h)][:limit]
    if not kept:
        return f"No Spotlight results for {query!r}."
    suffix = f"\n[showing {limit} of {len(hits)}]" if len(hits) > limit else ""
    return "\n".join(kept) + suffix


@mcp.tool(
    meta=tool_meta(Tier.WRITE, Escalation.OVERWRITES_EXISTING_FILE),
    description=(
        "Write a text file. mode=create fails if it exists; mode=overwrite replaces it "
        "(requires confirmation when the file already exists); mode=append adds to it."
    ),
)
def write_file(
    path: str,
    content: str,
    mode: Literal["create", "overwrite", "append"] = "create",
) -> str:
    target = assert_path_allowed(path)
    exists = target.exists()
    if mode == "create" and exists:
        raise ToolFailure(f"{target} already exists - use mode=overwrite or mode=append")
    if not target.parent.is_dir():
        raise ToolFailure(f"directory does not exist: {target.parent}")

    if mode == "append":
        with target.open("a", encoding="utf-8") as fh:
            fh.write(content)
        return f"Appended {len(content):,} characters to {target}."

    # Write to a temp file in the same directory, then replace: a crash mid-write
    # cannot leave a half-written file where a complete one used to be.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as tmp:
        tmp.write(content)
        staged = Path(tmp.name)
    staged.replace(target)
    verb = "Overwrote" if exists else "Wrote"
    return f"{verb} {target} ({len(content):,} characters)."


@mcp.tool(
    meta=tool_meta(Tier.CONFIRM),
    description="Move a file or folder to the Trash. Recoverable - never uses rm.",
)
def move_to_trash(path: str) -> str:
    target = assert_path_allowed(path)
    if not target.exists():
        raise ToolFailure(f"nothing at {path}")
    # Finder, so the item lands in the Trash with a Put Back entry.
    posix = str(target).replace("\\", "\\\\").replace('"', '\\"')
    _osascript(f'tell application "Finder" to delete POSIX file "{posix}"')
    return f"Moved {target} to the Trash."


# --------------------------------------------------------------------------- desktop


@mcp.tool(meta=tool_meta(Tier.WRITE), description="Show a macOS notification.")
def notify(title: str, body: str = "") -> str:
    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    _osascript(f'display notification "{escape(body)}" with title "{escape(title)}"')
    return f"Notified: {title}"


@mcp.tool(
    meta=tool_meta(Tier.READ),
    annotations=READ_ONLY,
    description="Read the clipboard.",
)
def clipboard_get() -> str:
    return _run(["pbpaste"]) or "(clipboard is empty)"


@mcp.tool(meta=tool_meta(Tier.WRITE), description="Put text on the clipboard.")
def clipboard_set(text: str) -> str:
    _run(["pbcopy"], stdin=text)
    return f"Copied {len(text):,} characters to the clipboard."


@mcp.tool(
    meta=tool_meta(Tier.READ),
    annotations=READ_ONLY,
    description=(
        "Capture the screen. region is 'x,y,width,height' in points; omit for the "
        "whole screen. Returns a PNG."
    ),
)
def screenshot(region: str | None = None) -> Image:
    argv = ["screencapture", "-x"]  # -x: no shutter sound
    if region:
        parts = region.replace(" ", "").split(",")
        if len(parts) != 4 or not all(p.lstrip("-").isdigit() for p in parts):
            raise ToolFailure("region must be 'x,y,width,height'")
        argv += ["-R", ",".join(parts)]
    with tempfile.TemporaryDirectory() as tmpdir:
        shot = Path(tmpdir) / "screen.png"
        try:
            _run([*argv, str(shot)])
        except ToolFailure as exc:
            raise ToolFailure(
                f"screencapture failed ({exc}). This is almost always the Screen "
                "Recording permission: grant it to the app running Jervis in "
                "System Settings > Privacy & Security > Screen Recording, then "
                "restart that app. Run scripts/doctor.sh to confirm."
            ) from exc
        if not shot.is_file():
            raise ToolFailure("screencapture produced no file - is Screen Recording granted?")
        return Image(data=shot.read_bytes(), format="png")


@mcp.tool(
    meta=tool_meta(Tier.READ),
    annotations=READ_ONLY,
    description="Battery, volume, frontmost app, Wi-Fi network and free disk space.",
)
def system_info() -> str:
    info: dict[str, Any] = {}

    try:
        batt = _run(["pmset", "-g", "batt"])
        info["battery"] = next(
            (part.strip() for part in batt.split(";") if "%" in part), batt.strip()
        )
    except ToolFailure:
        info["battery"] = "unavailable"

    for label, script in (
        ("volume", "output volume of (get volume settings)"),
        (
            "frontmost_app",
            'tell application "System Events" to name of first '
            "application process whose frontmost is true",
        ),
    ):
        try:
            info[label] = _osascript(script)
        except ToolFailure as exc:
            info[label] = f"unavailable ({exc})"

    # Needs Location Services on recent macOS; report the refusal rather than hide it.
    try:
        wifi = _run(["networksetup", "-getairportnetwork", "en0"]).strip()
        info["wifi"] = wifi.split(": ", 1)[1] if ": " in wifi else wifi
    except ToolFailure as exc:
        info["wifi"] = f"unavailable ({exc})"

    usage = shutil.disk_usage("/")
    info["disk_free"] = (
        f"{usage.free / 1_000_000_000:.1f} GB of {usage.total / 1_000_000_000:.0f} GB"
    )
    info["hostname"] = os.uname().nodename

    return json.dumps(info, indent=2)


@mcp.tool(meta=tool_meta(Tier.WRITE), description="Set output volume, 0-100.")
def set_volume(pct: int) -> str:
    if not 0 <= pct <= 100:
        raise ToolFailure("volume must be between 0 and 100")
    _osascript(f"set volume output volume {pct}")
    return f"Volume set to {pct}%."


@mcp.tool(
    meta=tool_meta(Tier.WRITE),
    description=(
        "Toggle Do Not Disturb. Requires a Shortcut named 'Toggle Do Not Disturb' in "
        "the Shortcuts app - macOS exposes no scriptable Focus API."
    ),
)
def toggle_do_not_disturb(shortcut_name: str = "Toggle Do Not Disturb") -> str:
    try:
        _run(["shortcuts", "run", shortcut_name])
    except ToolFailure as exc:
        raise ToolFailure(
            f"could not run the {shortcut_name!r} shortcut ({exc}). macOS has no "
            "scriptable Focus API; create a Shortcut with that name that toggles "
            "Do Not Disturb, then try again."
        ) from exc
    return f"Ran the {shortcut_name!r} shortcut."
