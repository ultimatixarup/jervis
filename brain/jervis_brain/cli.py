"""`jervis` - typed access to the brain, for testing without a microphone."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click

from .config import load_config
from .credentials import detect
from .permissions import AuditLog
from .server import Runtime, build_runtime


async def _with_runtime(fn: Any, *, needs_api_key: bool = False) -> Any:
    if needs_api_key:
        credential = detect()
        if not credential.ok:
            raise click.ClickException(
                f"No Anthropic credentials ({credential.detail}). Either put "
                "ANTHROPIC_API_KEY in ~/.jervis/.env, or run `ant auth login`. "
                "scripts/doctor.sh will confirm."
            )
    runtime = await build_runtime()
    try:
        if runtime.pool.failures:
            click.secho(f"warning: {runtime.pool.failures}", fg="yellow", err=True)
        return await fn(runtime)
    finally:
        await runtime.pool.stop()
        runtime.memory.close()


def _say(text: str) -> None:
    click.echo(text)


@click.group()
def cli() -> None:
    """Jervis, from the keyboard."""


@cli.command()
@click.argument("text", nargs=-1, required=True)
@click.option("--session", default="cli", help="Session id, for conversation continuity.")
def ask(text: tuple[str, ...], session: str) -> None:
    """Ask Jervis something."""

    async def run(runtime: Runtime) -> None:
        turn = await runtime.agent.ask(" ".join(text), session_id=session)
        _say(turn.reply)
        if turn.awaiting_confirmation:
            answer = click.prompt("  [yes/no]", default="no")
            follow_up = await runtime.agent.confirm(answer, session_id=session)
            _say(follow_up.reply)

    asyncio.run(_with_runtime(run, needs_api_key=True))


@cli.command()
@click.option("--session", default="repl", help="Session id.")
def repl(session: str) -> None:
    """Talk to Jervis in a loop. Ctrl-D to leave."""

    async def run(runtime: Runtime) -> None:
        click.secho(f"{runtime.config.persona_name} is listening. Ctrl-D to stop.", fg="green")
        while True:
            try:
                line = click.prompt("you", prompt_suffix="> ")
            except (EOFError, click.Abort):
                click.echo()
                return
            if not line.strip():
                continue
            turn = await runtime.agent.ask(line, session_id=session)
            click.secho(turn.reply, fg="cyan")
            while turn.awaiting_confirmation:
                answer = click.prompt("  [yes/no]", default="no")
                turn = await runtime.agent.confirm(answer, session_id=session)
                click.secho(turn.reply, fg="cyan")

    asyncio.run(_with_runtime(run, needs_api_key=True))


@cli.command()
def status() -> None:
    """Show what Jervis can reach and what it last did."""

    async def run(runtime: Runtime) -> None:
        click.echo(f"model:   {runtime.config.model}")
        click.echo(f"servers: {', '.join(s.name for s in runtime.config.enabled_servers)}")
        click.echo(f"tools:   {len(runtime.pool.tools)}")
        for name, error in runtime.pool.failures.items():
            click.secho(f"  FAILED {name}: {error}", fg="red")
        entries = AuditLog(runtime.config.paths.audit_log).entries()[-5:]
        if entries:
            click.echo("recent tool calls:")
            for entry in entries:
                mark = "ok" if entry.ok else "!!"
                click.echo(f"  [{mark}] {entry.timestamp} {entry.tool} ({entry.tier})")

    asyncio.run(_with_runtime(run))


@cli.command()
@click.option("-n", "--count", default=20, help="How many lines.")
def audit(count: int) -> None:
    """Print the tail of the audit log."""
    config = load_config()
    entries = AuditLog(config.paths.audit_log).entries()[-count:]
    if not entries:
        click.echo(f"nothing logged yet at {config.paths.audit_log}")
        return
    for entry in entries:
        click.echo(json.dumps(entry.model_dump(), indent=None))


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
