"""Точка входа в приложение."""
from __future__ import annotations

import logging
import os
import sys
import tracemalloc

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

if os.getenv("RAM_REPORT_TRACEMALLOC", "1").strip().lower() not in ("0", "false", "no", "off"):
    tracemalloc.start()

from app.config import Config  # noqa: E402
from app.core.bot import MegaBot  # noqa: E402
from app.core.logger import setup_logging  # noqa: E402

logger = logging.getLogger("bot")


def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)

    bot = MegaBot(config)
    try:
        bot.run(config.token)
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    finally:
        logger.info("Завершение работы")


if __name__ == "__main__":
    main()
