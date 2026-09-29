"""Сервис модерации: warn-система и иерархия ролей."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from app.core.checks import can_moderate
from app.utils.time import parse_duration

if TYPE_CHECKING:
    from app.db.warns_repository import WarnsRepository
    from app.services.settings_service import SettingsService

from app.types import WarnRow

logger = logging.getLogger("bot.services")

SLOWMODE_SUGGESTIONS: tuple[str, ...] = ("off", "5s", "10s", "30s", "1m", "5m", "10m", "1h")
TIMEOUT_SUGGESTIONS: tuple[str, ...] = ("1m", "10m", "1h", "6h", "12h", "1d", "7d")


class ModerationService:
    def __init__(self, warns_repo: WarnsRepository, settings: SettingsService) -> None:
        self._warns = warns_repo
        self._settings = settings

    @staticmethod
    def can_moderate(me: discord.Member, target: discord.Member) -> bool:
        return can_moderate(me, target)

    async def warn(self, guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
        from datetime import UTC, datetime

        await self._warns.add(guild_id, user_id, moderator_id, reason, datetime.now(UTC).isoformat())
        return await self._warns.count_for_user(guild_id, user_id)

    async def warns_for_user(self, guild_id: int, user_id: int) -> list[WarnRow]:
        return await self._warns.list_for_user(guild_id, user_id)

    async def warn_count(self, guild_id: int, user_id: int) -> int:
        return await self._warns.count_for_user(guild_id, user_id)

    async def clear_warns(self, guild_id: int, user_id: int) -> int:
        return await self._warns.clear_for_user(guild_id, user_id)

    async def remove_warn(self, guild_id: int, warn_id: int) -> bool:
        return await self._warns.delete(guild_id, warn_id)

    async def get_warn(self, guild_id: int, warn_id: int) -> WarnRow | None:
        return await self._warns.get(guild_id, warn_id)

    async def all_warns(self, guild_id: int, limit: int = 300) -> list[WarnRow]:
        return await self._warns.list_for_guild(guild_id, limit)

    @staticmethod
    def parse_duration(value: str) -> int | None:
        return parse_duration(value)

    @staticmethod
    def parse_slowmode(value: str) -> int:
        if value.strip().lower() in {"off", "0", "0s"}:
            return 0
        seconds = parse_duration(value)
        return 0 if seconds is None else max(0, min(21600, seconds))
