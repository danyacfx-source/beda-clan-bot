"""Репозиторий тикетов."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.base_repository import BaseRepository
from app.types import TicketRow

if TYPE_CHECKING:
    from datetime import datetime


class TicketsRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        created_at: datetime,
        voice_channel_id: int | None = None,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO tickets (guild_id, channel_id, creator_id, created_at, voice_channel_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, channel_id, creator_id, created_at.isoformat(), voice_channel_id),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID тикета")
        return int(cursor.lastrowid)

    async def by_channel(self, channel_id: int) -> TicketRow | None:
        row = await self.db.fetchone("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
        return cast(TicketRow, dict(row)) if row else None

    async def has_open_by_creator(self, guild_id: int, creator_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT 1 FROM tickets WHERE guild_id = ? AND creator_id = ? AND status = 'open' LIMIT 1",
            (guild_id, creator_id),
        )
        return row is not None

    async def close(self, ticket_id: int, closed_at: datetime) -> None:
        await self.db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE ticket_id = ?",
            (closed_at.isoformat(), ticket_id),
        )

    async def get(self, ticket_id: int) -> TicketRow | None:
        row = await self.db.fetchone("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
        return cast(TicketRow, dict(row)) if row else None

    async def save_transcript(self, ticket_id: int, transcript: str) -> None:
        await self.db.execute(
            "UPDATE tickets SET transcript = ? WHERE ticket_id = ?",
            (transcript, ticket_id),
        )

    async def list_for_guild(self, guild_id: int, limit: int = 100) -> list[TicketRow]:
        rows = await self.db.fetchall(
            "SELECT * FROM tickets WHERE guild_id = ? ORDER BY ticket_id DESC LIMIT ?",
            (guild_id, limit),
        )
        return [cast(TicketRow, dict(row)) for row in rows]
