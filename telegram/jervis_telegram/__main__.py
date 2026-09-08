"""Run the Telegram bot: `python -m jervis_telegram`."""

from __future__ import annotations

import argparse
import logging
import sys

import httpx

from .api import TelegramAPI, TelegramError
from .bot import Bot
from .settings import NotConfigured, load, load_env

log = logging.getLogger("jervis.telegram")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jervis-telegram", description="Jervis, on Telegram.")
    parser.add_argument("--updates", type=int, default=None, help="Stop after N updates.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    load_env()
    settings = load()

    try:
        settings.require_usable()
    except NotConfigured as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    try:
        api = TelegramAPI(settings.token)
        identity = api.me()
    except (TelegramError, httpx.HTTPError) as exc:
        print(f"Could not reach Telegram: {exc}", file=sys.stderr)
        return 1

    handle = identity.get("username", "?")
    print(f"Connected as @{handle}.")
    print(f"Answering {len(settings.allowed_user_ids)} account(s); everyone else is refused.")

    bot = Bot(api=api, settings=settings)
    if not _brain_is_up(bot, settings.brain_url):
        print(
            f"Warning: no brain at {settings.brain_url}. Start it with `jervis serve`.",
            file=sys.stderr,
        )

    if settings.drop_pending_on_start:
        try:
            api.drop_pending()
        except (TelegramError, httpx.HTTPError):
            log.warning("could not clear the backlog", exc_info=True)

    print(f"Listening. Message @{handle} in Telegram. Ctrl-C to stop.")
    try:
        bot.run(max_updates=args.updates)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        bot.close()
    return 0


def _brain_is_up(bot: Bot, url: str) -> bool:
    try:
        bot.brain.get(f"{url}/health", timeout=5).raise_for_status()
    except (httpx.HTTPError, OSError):
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
