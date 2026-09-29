"""Сервис настроек серверов."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from app.db.settings_repository import _INT_COLUMNS

if TYPE_CHECKING:
    from app.core.bot import MegaBot
    from app.db.settings_repository import SettingsRepository


class SettingsService:
    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._ttl = 120.0

    async def ensure_all_guilds(self, bot: MegaBot) -> None:
        for guild in bot.guilds:
            await self._repo.ensure_row(guild.id)

    async def get(self, guild_id: int) -> dict[str, Any]:
        now = time.monotonic()
        cached = self._cache.get(guild_id)
        if cached is not None and cached[0] > now:
            return dict(cached[1])
        async with self._locks[guild_id]:
            now = time.monotonic()
            cached = self._cache.get(guild_id)
            if cached is not None and cached[0] > now:
                return dict(cached[1])
            settings = await self._repo.get(guild_id)
            for column in _INT_COLUMNS:
                raw = settings.get(column)
                settings[column] = int(raw) if raw else None
            settings["automod_enabled"] = bool(settings.get("automod_enabled"))
            self._cache[guild_id] = (now + self._ttl, dict(settings))
            return dict(settings)

    async def update(self, guild_id: int, **kwargs: Any) -> None:
        """Обновляет переданные колонки. ``None`` — осознанная очистка значения."""
        for column, value in kwargs.items():
            await self._repo.set(guild_id, column, value)
        self._cache.pop(guild_id, None)

    async def set_blocked_words(self, guild_id: int, words: list[str]) -> list[str]:
        """Полностью заменяет список запрещённых слов (нормализует и убирает дубли)."""
        normalized: list[str] = []
        for word in words:
            clean = str(word).strip().lower()
            if clean and clean not in normalized:
                normalized.append(clean)
        await self._repo.set(guild_id, "blocked_words", json.dumps(normalized, ensure_ascii=False))
        self._cache.pop(guild_id, None)
        return normalized

    async def blocked_words(self, guild_id: int) -> list[str]:
        settings = await self.get(guild_id)
        try:
            return list(json.loads(settings.get("blocked_words") or "[]"))
        except (TypeError, json.JSONDecodeError):
            return []

    async def add_blocked_word(self, guild_id: int, word: str) -> list[str]:
        words = await self.blocked_words(guild_id)
        normalized = word.strip().lower()
        if normalized and normalized not in words:
            words.append(normalized)
            await self._repo.set(guild_id, "blocked_words", json.dumps(words, ensure_ascii=False))
            self._cache.pop(guild_id, None)
        return words

    async def remove_blocked_word(self, guild_id: int, word: str) -> list[str]:
        words = await self.blocked_words(guild_id)
        normalized = word.strip().lower()
        if normalized in words:
            words.remove(normalized)
            await self._repo.set(guild_id, "blocked_words", json.dumps(words, ensure_ascii=False))
            self._cache.pop(guild_id, None)
        return words
