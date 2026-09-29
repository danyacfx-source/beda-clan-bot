"""Сервис логирования событий сервера в веб-ленту (вместо Discord-каналов)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from app.core.bot import MegaBot
    from app.services.settings_service import SettingsService

logger = logging.getLogger("bot.services")

_AUDIT_LOGGER = logger.getChild("audit")
_AUDIT_LOGGER.setLevel(logging.INFO)


def _embed_to_text(embed: discord.Embed) -> str:
    """Превращает эмбед в компактный текст для веб-ленты."""
    parts: list[str] = []
    if embed.title:
        parts.append(embed.title)
    if embed.description:
        parts.append(embed.description)
    for field in embed.fields:
        value = str(field.value or "").replace("\n", " ")
        parts.append(f"{field.name}: {value}")
    text = "\n".join(p for p in parts if p)
    return text[:2000] if text else "(пустое событие)"


class LoggingService:
    """Записывает аудит-события в веб-ленту вместо отправки в Discord-каналы."""

    def __init__(self, settings: SettingsService, bot: MegaBot) -> None:
        self._settings = settings
        self._bot = bot

    async def target_channel(self, guild: discord.Guild, *, category: str | None = None) -> discord.TextChannel | None:
        """Сохраняет совместимый интерфейс, но реальные каналы не используются."""
        return None

    async def send_embed(self, guild: discord.Guild, embed: discord.Embed, *, category: str | None = None) -> None:
        cat = category or "general"
        text = _embed_to_text(embed)
        guild_name = guild.name if guild is not None else "?"
        self._emit(cat, f"[{guild_name}] {text}")

    async def log_event(
        self,
        guild: discord.Guild,
        title: str,
        description: str | None = None,
        *,
        color: discord.Colour | None = None,
        author: discord.Member | discord.User | None = None,
    ) -> None:
        parts: list[str] = []
        if title:
            parts.append(title)
        if description:
            parts.append(description)
        if author is not None:
            parts.append(f"Инициатор: {author} ({author.id})")
        guild_name = guild.name if guild is not None else "?"
        self._emit("general", f"[{guild_name}] " + (" | ".join(parts) if parts else "(пустое событие)"))

    async def log_mod_action(
        self,
        guild: discord.Guild,
        action: str,
        target: discord.User | discord.Member,
        moderator: discord.Member,
        reason: str = "",
        description: str | None = None,
    ) -> None:
        parts: list[str] = []
        if description:
            parts.append(description)
        parts.append(f"Модерация: {action}")
        parts.append(f"Нарушитель: {target} ({target.id})")
        parts.append(f"Модератор: {moderator} ({moderator.id})")
        if reason:
            parts.append(f"Причина: {reason}")
        guild_name = guild.name if guild is not None else "?"
        self._emit("mod", f"[{guild_name}] " + " | ".join(parts))

    def _emit(self, category: str, text: str) -> None:
        """Пишет событие в логгер bot.audit.<category> — его ловит веб-лента панели."""
        try:
            _AUDIT_LOGGER.getChild(category).info(text)
        except Exception:
            logger.debug("Не удалось записать аудит-событие в ленту", exc_info=True)
