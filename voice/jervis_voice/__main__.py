"""Run the listening loop: `python -m jervis_voice`."""

from __future__ import annotations

import argparse
import logging
import sys

from .brain import BrainClient
from .loop import build
from .settings import load, load_env
from .wake import BUILTIN_MODELS

log = logging.getLogger("jervis.voice")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jervis-voice", description="Jervis, by voice.")
    parser.add_argument("--session", default="voice", help="Conversation session id.")
    parser.add_argument("--turns", type=int, default=None, help="Stop after N exchanges.")
    parser.add_argument("--no-chime", action="store_true", help="Do not play the listening chime.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    load_env()
    config, base_url = load()

    brain = BrainClient(base_url=base_url, session_id=args.session)
    print(f"Waiting for the brain at {base_url} ...")
    if not brain.wait_until_ready():
        print(
            f"The brain is not answering at {base_url}. Start it with "
            "`uv run jervis serve`, or use scripts/start.sh which starts both.",
            file=sys.stderr,
        )
        return 1

    health = brain.health()
    tools = health.get("tools")
    count = len(tools) if isinstance(tools, list) else 0
    print(f"Brain ready: {health.get('model')}, {count} tools.")

    loop = build(config, brain)
    print(f"Loading {config.stt_model} ...")
    warm = getattr(loop.transcriber, "warm_up", None)
    if warm:
        warm()

    phrase = "Hey Jarvis" if config.wake_model == "hey_jarvis" else config.wake_model
    print(f'Listening. Say "{phrase}", then your request. Ctrl-C to stop.')
    if config.wake_model in BUILTIN_MODELS and config.wake_model != "jervis":
        print(
            f"  (the wake word is the built-in {config.wake_model!r} model - see "
            "voice/jervis_voice/models/README.md to train a 'jervis' one)"
        )

    loop.chime = not args.no_chime
    try:
        loop.run(max_turns=args.turns)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        loop.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
