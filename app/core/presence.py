"""Присутствие бота: нейтральный игровой статус без привязки к стримам."""

from __future__ import annotations

import discord

DEFAULT_STATUS = "на связи"


def bot_activity(status: str | None = None) -> discord.Activity:
    """Custom-статус вида «играет с кодом» — без красного кружка стрима."""
    text = (status or DEFAULT_STATUS).strip() or DEFAULT_STATUS
    return discord.Activity(type=discord.ActivityType.custom, name=text[:128])
