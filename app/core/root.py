"""Корень приложения: всё, чем владеет процесс бота (результат composition root)."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Config
from app.core.bot import MegaBot
from app.db.database import Database
from app.services import Services


@dataclass(slots=True)
class Root:
    """Единая точка владения графом зависимостей.

    ``db`` — низкоуровневая деталь; потребители работают с ``services``
    и портами из app/core/ports.py, а не с БД напрямую.
    """

    config: Config
    db: Database
    bot: MegaBot
    services: Services
