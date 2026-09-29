"""Настройка логирования: консоль + ротация файлов. Идемпотентно."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_NOISY_LOGGERS = ("discord.gateway", "discord.client", "httpcore")


def setup_logging(level: str = "INFO", log_dir: str | Path | None = None, *, to_file: bool = True) -> None:
    """Настраивает корневой логгер. Повторные вызовы безопасны (не дублирует хендлеры)."""
    root = logging.getLogger()
    level = level.upper()
    root.setLevel(level)

    if not getattr(root, "_mega_bot_configured", False):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(console)

        if to_file:
            directory = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(directory / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(_FORMAT))
            root.addHandler(file_handler)

        setattr(root, "_mega_bot_configured", True)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("bot").debug("Логгер инициализирован, уровень %s", level)
