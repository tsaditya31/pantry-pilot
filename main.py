"""
Pantry Pilot — Shopping Intelligence Telegram Bot

Usage:
  python main.py bot    # Start Telegram bot (long-polling)
"""

import argparse
import logging
import sys

from config import settings
from db.store import init_db
from bot.telegram_bot import run_polling_loop
from core.restock_checker import run_daily_restock_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def cmd_bot():
    """Start the interactive Telegram chatbot (long-polling loop)."""
    logger.info("=== Starting Pantry Pilot bot ===")
    init_db()
    run_polling_loop()


def cmd_restock():
    """Run a one-shot restock check for all users."""
    logger.info("=== Running restock check ===")
    init_db()
    run_daily_restock_check()
    logger.info("=== Restock check complete ===")


def main():
    parser = argparse.ArgumentParser(description="Pantry Pilot — Shopping Intelligence Bot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bot", help="Start Telegram bot (long-polling)")
    subparsers.add_parser("restock", help="Run one-shot restock check for all users")

    args = parser.parse_args()

    if args.command == "bot":
        cmd_bot()
    elif args.command == "restock":
        cmd_restock()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
