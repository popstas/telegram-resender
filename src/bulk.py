"""Bulk operations CLI for folder chats.

Usage:
    python -m src.bulk --folder <folder_name> --mute
    python -m src.bulk --folder <folder_name> --add-user <username>
    python -m src.bulk --folder <folder_name> --remove-user <username>
"""

import argparse
import asyncio
import logging

from telethon import TelegramClient

from . import telegram_utils
from .app import setup_logging
from .config import get_api_credentials, load_config, parse_proxy

logger = logging.getLogger(__name__)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Bulk operations on folder chats")
    parser.add_argument("--folder", required=True, help="Folder name to operate on")
    parser.add_argument("--mute", action="store_true", help="Mute all chats in folder")
    parser.add_argument("--add-user", help="Username to add to all chats in folder")
    parser.add_argument(
        "--remove-user", help="Username to remove from all chats in folder"
    )
    return parser.parse_args(argv)


async def run(args) -> None:
    config = load_config()
    setup_logging(config.get("log_level", "info"))

    api_id, api_hash, session_name = get_api_credentials(config)
    proxy = parse_proxy(config["proxy_url"]) if config.get("proxy_url") else None
    client = TelegramClient(session_name, api_id, api_hash, proxy=proxy)
    telegram_utils.client = client
    await client.start()

    try:
        if args.mute:
            logger.info("Muting chats in folder '%s'", args.folder)
            await telegram_utils.mute_chats_from_folders([args.folder])

        if args.add_user:
            logger.info(
                "Adding '%s' to chats in folder '%s'", args.add_user, args.folder
            )
            await telegram_utils.add_user_to_folder_chats(args.folder, args.add_user)

        if args.remove_user:
            logger.info(
                "Removing '%s' from chats in folder '%s'",
                args.remove_user,
                args.folder,
            )
            await telegram_utils.remove_user_from_folder_chats(
                args.folder, args.remove_user
            )
    finally:
        await client.disconnect()


def main(argv=None):
    args = parse_args(argv)
    if not args.mute and not args.add_user and not args.remove_user:
        logger.error(
            "No action specified. Use --mute, --add-user and/or --remove-user."
        )
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
