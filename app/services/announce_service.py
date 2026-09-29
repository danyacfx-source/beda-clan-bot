"""Мастер объявлений: хранение выбранного адресата между сообщениями и рестартами."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.core.base import BaseService
from app.db.kv_repository import KvRepository

logger = logging.getLogger("bot")

_KEY_PREFIX = "announce:target:"


@dataclass(frozen=True, slots=True)
class AnnounceTarget:
    """Куда мастер публикует объявление: гильдия и канал."""

    guild_id: int
    channel_id: int


class AnnounceService(BaseService[KvRepository]):
    """Состояние мастера объявлений.

    Адресат лежит в ``kv``, а не в памяти кога: мастер — это диалог из
    нескольких сообщений в личке, и он обязан пережить рестарт бота между
    шагами «выбрали канал» и «прислали текст».
    """

    def _key(self, user_id: int) -> str:
        return f"{_KEY_PREFIX}{user_id}"

    async def get_target(self, user_id: int) -> AnnounceTarget | None:
        raw = await self.repo.get(self._key(user_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return AnnounceTarget(guild_id=int(data["guild_id"]), channel_id=int(data["channel_id"]))
        except (ValueError, KeyError, TypeError):
            # Испорченная запись не должна ломать диалог — просто начинаем заново.
            logger.warning("Повреждённая запись адресата объявления для %s", user_id, exc_info=True)
            await self.clear_target(user_id)
            return None

    async def set_target(self, user_id: int, target: AnnounceTarget) -> None:
        payload = json.dumps({"guild_id": target.guild_id, "channel_id": target.channel_id})
        await self.repo.set(self._key(user_id), payload)

    async def clear_target(self, user_id: int) -> None:
        await self.repo.delete(self._key(user_id))
