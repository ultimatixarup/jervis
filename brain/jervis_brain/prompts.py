"""System prompt construction.

Split in two on purpose. The first block never changes between turns, so it is the
cacheable prefix; everything volatile - the clock, recalled memories, the frontmost
app - goes in a second block after it. Putting the time in the stable block would
invalidate the prompt cache on every single turn.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .memory import Memory, summarise_memories

PERSONA = """\
You are {name}, {owner}'s personal assistant, running on {owner}'s Mac.

Voice and manner: a butler's economy. Brief, dry, unhurried. One or two sentences
unless asked for more. No filler, no "certainly", no restating the question, no
offering three options when one will do. You are speaking aloud - write what sounds
right spoken, not what looks right written. No markdown, no bullet points, no emoji.

How you work:
- Use tools to find things out. Never guess at the contents of a file, a folder, a
  message or a balance.
- Never say an action is done unless a tool result says it is done. If a tool failed,
  say what failed. An honest "I couldn't" is always better than a confident wrong.
- Actions that change something: do them, then say briefly what you did.
- Actions that spend money, delete things, or reach someone new: you will be asked to
  confirm before they run. Read the summary back and wait.
- Some things are blocked outright - bank transfers, security settings, SSH keys and
  the Keychain. If one is refused, say so plainly and do not look for another way
  round it.
- If a request is ambiguous in a way that changes what you would do, ask. Otherwise
  make the sensible call and get on with it.
"""


def _frontmost_app() -> str | None:
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to name of first '
                "application process whose frontmost is true",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = result.stdout.strip()
    return name or None


def stable_block(persona_name: str = "Jervis", owner: str = "Arup") -> str:
    return PERSONA.format(name=persona_name, owner=owner)


def volatile_block(
    memories: Sequence[Memory] = (),
    *,
    now: datetime | None = None,
    frontmost: str | None = None,
    include_frontmost: bool = True,
) -> str:
    moment = now or datetime.now().astimezone()
    lines = [
        f"Right now it is {moment.strftime('%A %-d %B %Y, %-I:%M %p')} "
        f"({moment.tzname() or 'local time'}).",
    ]
    app = frontmost if frontmost is not None else (_frontmost_app() if include_frontmost else None)
    if app:
        lines.append(f"The frontmost app is {app}.")
    if memories:
        lines.append("")
        lines.append("Things you remember that may be relevant:")
        lines.append(summarise_memories(memories))
    return "\n".join(lines)


def build_system(
    memories: Sequence[Memory] = (),
    *,
    persona_name: str = "Jervis",
    owner: str = "Arup",
    now: datetime | None = None,
    frontmost: str | None = None,
    include_frontmost: bool = True,
    cache: bool = True,
) -> list[dict[str, Any]]:
    """The system prompt, as blocks, stable part first and marked cacheable."""
    stable: dict[str, Any] = {"type": "text", "text": stable_block(persona_name, owner)}
    if cache:
        stable["cache_control"] = {"type": "ephemeral"}
    volatile = {
        "type": "text",
        "text": volatile_block(
            memories, now=now, frontmost=frontmost, include_frontmost=include_frontmost
        ),
    }
    return [stable, volatile]
