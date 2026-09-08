"""`jervis` - Jervis, from the keyboard."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click

from . import daemon, tracing
from .agent import Turn
from .config import load_config
from .credentials import detect
from .history import readline_history
from .permissions import AuditLog
from .prompts import Channel
from .render import ConsoleRenderer
from .server import Runtime, build_runtime

CONFIRM_HELP = "yes / no"


async def _with_runtime(fn: Any, *, needs_api_key: bool = False) -> Any:
    if needs_api_key:
        credential = detect()
        if not credential.ok:
            raise click.ClickException(
                f"No Anthropic credentials ({credential.detail}). Either put "
                "ANTHROPIC_API_KEY in ~/.jervis/.env, or run `ant auth login`. "
                "scripts/doctor.sh will confirm."
            )
    runtime = await build_runtime(channel=Channel.TEXT)
    try:
        if runtime.pool.failures:
            click.secho(f"warning: {runtime.pool.failures}", fg="yellow", err=True)
        return await fn(runtime)
    finally:
        await runtime.pool.stop()
        runtime.memory.close()
        tracing.shutdown()


def _ask_confirmation(_summary: str) -> str:
    """Ask about a pending action. EOF or Ctrl-C means no, not a traceback.

    The summary is not reprinted: the paused turn's reply already read it back, and
    saying it twice makes the prompt look like a second, different question.
    """
    try:
        return click.prompt(f"  [{CONFIRM_HELP}]", default="no", show_default=False)
    except (EOFError, click.Abort):
        click.echo()
        return "no"


async def _exchange(runtime: Runtime, text: str, session: str) -> Turn:
    """One complete exchange, including however many confirmations it takes.

    Shared by `ask` and `repl` so the confirmation handling cannot drift apart again -
    `ask` used to answer only the first one and drop the rest silently.
    """
    renderer = ConsoleRenderer()
    turn = await runtime.agent.ask(text, session_id=session, on_event=renderer)
    renderer.finish(turn)

    while turn.awaiting_confirmation:
        answer = _ask_confirmation(turn.pending_confirmation or "")
        renderer = ConsoleRenderer()
        turn = await runtime.agent.confirm(answer, session_id=session, on_event=renderer)
        renderer.finish(turn)
    return turn


@click.group()
def cli() -> None:
    """Jervis, from the keyboard."""


@cli.command()
@click.argument("text", nargs=-1, required=True)
@click.option("--session", default="cli", help="Session id, for conversation continuity.")
def ask(text: tuple[str, ...], session: str) -> None:
    """Ask Jervis something."""

    async def run(runtime: Runtime) -> None:
        await _exchange(runtime, " ".join(text), session)

    asyncio.run(_with_runtime(run, needs_api_key=True))


BANNER = "{name} is ready. /help for commands, Ctrl-D to leave."

META_HELP = """\
  /help            this
  /tools           what Jervis can reach
  /audit [n]       the last n tool calls (default 5)
  /session [id]    show, or switch to, a conversation
  /clear           forget this conversation
  /quit            leave
"""


class Repl:
    """The prompt loop. Meta-commands never reach the model."""

    def __init__(self, runtime: Runtime, session: str) -> None:
        self.runtime = runtime
        self.session = session
        self.done = False

    async def handle(self, line: str) -> None:
        if line.startswith("/"):
            self.meta(line)
            return
        await _exchange(self.runtime, line, self.session)

    def meta(self, line: str) -> None:
        command, _, argument = line[1:].partition(" ")
        argument = argument.strip()
        match command.lower():
            case "help" | "?":
                click.echo(META_HELP, nl=False)
            case "tools":
                for tool in self.runtime.pool.tools:
                    click.echo(f"  {tool.qualified}")
            case "audit":
                _print_audit(self.runtime.config.paths.audit_log, _int_or(argument, 5))
            case "session":
                if argument:
                    self.session = argument
                    click.secho(f"  now in session {argument!r}", fg="green")
                else:
                    click.echo(f"  session {self.session!r}")
            case "clear":
                self.runtime.memory.replace_history(self.session, [])
                click.secho(f"  forgot session {self.session!r}", fg="green")
            case "quit" | "exit":
                self.done = True
            case _:
                click.secho(f"  no such command: /{command}. Try /help.", fg="red")


@cli.command()
@click.option("--session", default="repl", help="Session id.")
def repl(session: str) -> None:
    """Talk to Jervis in a loop. Ctrl-D to leave."""

    async def run(runtime: Runtime) -> None:
        click.secho(BANNER.format(name=runtime.config.persona_name), fg="green")
        loop = Repl(runtime, session)
        with readline_history(runtime.config.paths.history):
            while not loop.done:
                try:
                    line = click.prompt("you", prompt_suffix="> ")
                except (EOFError, click.Abort):
                    click.echo()
                    return
                if not line.strip():
                    continue
                await loop.handle(line.strip())

    asyncio.run(_with_runtime(run, needs_api_key=True))


@cli.command()
def status() -> None:
    """Show what Jervis can reach and what it last did."""

    async def run(runtime: Runtime) -> None:
        click.echo(f"model:   {runtime.config.model}")
        click.echo(f"servers: {', '.join(s.name for s in runtime.config.enabled_servers)}")
        click.echo(f"tools:   {len(runtime.pool.tools)}")

        repo = Path(__file__).resolve().parents[2]
        for agent in (
            daemon.brain_agent(repo, runtime.config.paths.home),
            daemon.telegram_agent(repo, runtime.config.paths.home),
        ):
            state = daemon.describe(agent)
            colour = "green" if state.startswith("running") else None
            click.secho(f"daemon:  {agent.label} - {state}", fg=colour)

        for name, error in runtime.pool.failures.items():
            click.secho(f"  FAILED {name}: {error}", fg="red")
        entries = AuditLog(runtime.config.paths.audit_log).entries()[-5:]
        if entries:
            click.echo("recent tool calls:")
            for entry in entries:
                mark = "ok" if entry.ok else "!!"
                click.echo(f"  [{mark}] {entry.timestamp} {entry.tool} ({entry.tier})")

    asyncio.run(_with_runtime(run))


def _int_or(value: str, fallback: int) -> int:
    try:
        return int(value)
    except ValueError:
        return fallback


def _print_audit(path: Any, count: int) -> None:
    entries = AuditLog(path).entries()[-count:]
    if not entries:
        click.echo(f"  nothing logged yet at {path}")
        return
    for entry in entries:
        mark = click.style("ok", fg="green") if entry.ok else click.style("!!", fg="red")
        confirmed = " (confirmed)" if entry.confirmed else ""
        click.echo(
            f"  [{mark}] {entry.tool} ({entry.tier}){confirmed}  {entry.result_summary[:60]}"
        )


@cli.command()
@click.option("-n", "--count", default=20, help="How many lines.")
@click.option("--json", "as_json", is_flag=True, help="Raw JSON lines, for piping.")
def audit(count: int, as_json: bool) -> None:
    """Print the tail of the audit log."""
    config = load_config()
    if not as_json:
        _print_audit(config.paths.audit_log, count)
        return
    entries = AuditLog(config.paths.audit_log).entries()[-count:]
    for entry in entries:
        click.echo(json.dumps(entry.model_dump()))


@cli.command()
def serve() -> None:
    """Run the HTTP endpoint in the foreground."""
    from .server import main

    main()


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
