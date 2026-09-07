"""MCP server: let Jervis delegate work to Claude Code.

Claude Code can read and edit whatever it is pointed at, so this server is narrow on
purpose:

- `ask` is genuinely read-only: plan mode, and Read/Glob/Grep as the only tools.
- `run_task` can edit files, so it is `confirm` tier - Jervis reads the instruction
  back and waits for a spoken yes.
- Where it may run comes from ~/.jervis/config.yaml, never from a tool argument.
- Permission bypass flags are never passed. There is a test that greps this file to
  keep it that way.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .settings import ALWAYS_DENIED, Settings, list_projects, load_settings, resolve_project

__all__ = ["ALWAYS_DENIED", "mcp"]

TIER_KEY = "x-jervis-tier"
READ_ONLY = ToolAnnotations(read_only_hint=True)

mcp = MCPServer(
    "jervis-claudecode",
    version=__version__,
    instructions=(
        "Delegates coding work to Claude Code inside Arup's own project directories. "
        "Use `ask` to find something out and `run_task` to change code."
    ),
)


def _settings() -> Settings:
    return load_settings()


def _claude_binary() -> str:
    found = shutil.which("claude")
    if not found:
        raise ToolError(
            "The Claude Code CLI is not on PATH. Install it, or add its directory to "
            "the PATH the Jervis agent runs with."
        )
    return found


def _run_claude(
    prompt: str,
    project: Path,
    *,
    allowed_tools: tuple[str, ...],
    permission_mode: str,
    timeout: int,
    settings: Settings,
    session_id: str | None = None,
) -> dict[str, Any]:
    argv = [
        _claude_binary(),
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        permission_mode,
        "--max-turns",
        str(settings.max_turns),
        "--allowedTools",
        *allowed_tools,
        "--disallowedTools",
        *ALWAYS_DENIED,
    ]
    if settings.model:
        argv += ["--model", settings.model]
    if session_id:
        argv += ["--resume", session_id]

    try:
        # The prompt goes on stdin, never as a trailing argument: --allowedTools and
        # --disallowedTools are variadic, so a positional prompt after them is
        # swallowed as another tool name and Claude Code exits saying it got no input.
        proc = subprocess.run(
            argv,
            cwd=str(project),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            f"Claude Code was still working after {timeout}s, so I stopped it. Nothing "
            "it had already written to disk is undone - check `git status` in "
            f"{project.name}."
        ) from exc

    if proc.returncode != 0 and not proc.stdout.strip():
        detail = (proc.stderr or "").strip()[:400] or f"exit {proc.returncode}"
        raise ToolError(f"Claude Code failed to start: {detail}")

    try:
        payload: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Could not read Claude Code's reply: {proc.stdout[:300]!r}") from exc
    return payload


def _format(payload: dict[str, Any], project: Path) -> str:
    """Report the answer, what it cost, and anything it was refused."""
    result = str(payload.get("result", "")).strip() or "(no output)"
    lines = [result, ""]

    if payload.get("is_error"):
        lines.insert(0, f"Claude Code reported a problem ({payload.get('subtype', 'error')}).")

    cost = payload.get("total_cost_usd")
    turns = payload.get("num_turns")
    meta = [f"project {project.name}"]
    if isinstance(turns, int):
        meta.append(f"{turns} turn{'s' if turns != 1 else ''}")
    if isinstance(cost, int | float):
        meta.append(f"${cost:.2f}")
    if payload.get("session_id"):
        meta.append(f"session {payload['session_id']}")
    lines.append(f"[{', '.join(meta)}]")

    denials = payload.get("permission_denials") or []
    if denials:
        names = sorted({str(d.get("tool_name", d)) for d in denials})
        lines.append(
            f"[it wanted to use {', '.join(names)} but is not allowed to; add them to "
            "claudecode.extra_allowed_tools in ~/.jervis/config.yaml if that is wanted]"
        )
    return "\n".join(lines).strip()


@mcp.tool(
    meta={TIER_KEY: "read"},
    annotations=READ_ONLY,
    description="List the project directories Claude Code may work in.",
)
def list_code_projects() -> str:
    settings = _settings()
    projects = list_projects(settings)
    if not projects:
        roots = ", ".join(str(p) for p in settings.projects) or "(none configured)"
        return f"No projects found under {roots}."
    return "\n".join(f"{p.name}  ({p})" for p in projects)


@mcp.tool(
    meta={TIER_KEY: "read"},
    annotations=READ_ONLY,
    description=(
        "Ask Claude Code a question about a codebase. Read-only: it can read, glob and "
        "grep, and cannot edit anything. Use this for 'what does X do', 'where is Y', "
        "'why is the build failing'."
    ),
)
def ask(question: str, project: str, session_id: str | None = None) -> str:
    settings = _settings()
    target = _resolve(project, settings)
    payload = _run_claude(
        question,
        target,
        allowed_tools=settings.allowed_tools(editing=False),
        permission_mode="plan",
        timeout=settings.ask_timeout_seconds,
        settings=settings,
        session_id=session_id,
    )
    return _format(payload, target)


@mcp.tool(
    meta={TIER_KEY: "confirm"},
    description=(
        "Have Claude Code make a change: fix a bug, write a test, refactor. It edits "
        "files in the project. It cannot run shell commands unless Arup has "
        "allow-listed them, and it never pushes. Requires spoken confirmation."
    ),
)
def run_task(instruction: str, project: str, session_id: str | None = None) -> str:
    settings = _settings()
    target = _resolve(project, settings)
    payload = _run_claude(
        instruction,
        target,
        allowed_tools=settings.allowed_tools(editing=True),
        permission_mode="acceptEdits",
        timeout=settings.task_timeout_seconds,
        settings=settings,
        session_id=session_id,
    )
    return _format(payload, target)


@mcp.tool(
    meta={TIER_KEY: "read"},
    annotations=READ_ONLY,
    description="Show uncommitted changes in a project, so you can hear what Claude Code did.",
)
def review_changes(project: str) -> str:
    settings = _settings()
    target = _resolve(project, settings)
    try:
        proc = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(f"could not read git status in {target}: {exc}") from exc
    if proc.returncode != 0:
        return f"{target.name} is not a git repository."
    changed = proc.stdout.strip()
    return changed or f"{target.name}: no uncommitted changes."


def _resolve(project: str, settings: Settings) -> Path:
    try:
        return resolve_project(project, settings)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
