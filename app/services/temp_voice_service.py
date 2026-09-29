"""Сервис временных голосовых каналов."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.base import BaseService
from app.db.temp_voices_repository import TempVoicesRepository
from app.types import TempVoiceRow

if TYPE_CHECKING:
    from datetime import datetime

    from app.db.temp_voices_repository import TempVoicesRepository


class TempVoiceService(BaseService[TempVoicesRepository]):
    repo: TempVoicesRepository

    def __init__(self, repo: TempVoicesRepository) -> None:
        super().__init__(repo)

    async def create(self, owner_id: int, channel_id: int, created_at: datetime) -> None:
        await self._repo.create(owner_id, channel_id, created_at)

    async def owner_of(self, channel_id: int) -> int | None:
        return await self._repo.owner_of_channel(channel_id)

    async def channel_of_owner(self, owner_id: int) -> int | None:
        return await self._repo.channel_of_owner(owner_id)

    async def delete(self, channel_id: int) -> bool:
        return await self._repo.delete_by_channel(channel_id)

    async def transfer(self, channel_id: int, new_owner_id: int) -> None:
        await self._repo.delete_by_channel(channel_id)
        await self._repo.create(new_owner_id, channel_id, datetime.now(UTC))

    async def all(self) -> list[TempVoiceRow]:
        return await self._repo.all()
