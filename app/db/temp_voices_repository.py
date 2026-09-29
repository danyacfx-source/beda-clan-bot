"""Репозиторий временных голосовых каналов (владелец → канал)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.base_repository import BaseRepository
from app.types import TempVoiceRow

if TYPE_CHECKING:
    from datetime import datetime


class TempVoicesRepository(BaseRepository):
    async def create(self, owner_id: int, channel_id: int, created_at: datetime) -> None:
        await self.db.execute(
            "INSERT INTO temp_voices (owner_id, channel_id, created_at) VALUES (?, ?, ?)",
            (owner_id, channel_id, created_at.isoformat()),
        )

    async def channel_of_owner(self, owner_id: int) -> int | None:
        row = await self.db.fetchone("SELECT channel_id FROM temp_voices WHERE owner_id = ?", (owner_id,))
        return int(row["channel_id"]) if row else None

    async def owner_of_channel(self, channel_id: int) -> int | None:
        row = await self.db.fetchone("SELECT owner_id FROM temp_voices WHERE channel_id = ?", (channel_id,))
        return int(row["owner_id"]) if row else None

    async def delete_by_channel(self, channel_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM temp_voices WHERE channel_id = ?", (channel_id,))
        return cursor.rowcount > 0

    async def delete_by_owner(self, owner_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM temp_voices WHERE owner_id = ?", (owner_id,))
        return cursor.rowcount > 0

    async def all(self) -> list[TempVoiceRow]:
        rows = await self.db.fetchall("SELECT owner_id, channel_id, created_at FROM temp_voices")
        return [cast(TempVoiceRow, dict(row)) for row in rows]
