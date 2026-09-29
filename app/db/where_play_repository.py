"""Репозиторий карточки «Где играем» (where_play, caller_rooms)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.base_repository import BaseRepository
from app.types import CallerRoomRow, WherePlayRow

if TYPE_CHECKING:
    from datetime import datetime


class WherePlayRepository(BaseRepository):
    async def upsert_channel(self, guild_id: int, channel_id: int, role_id: int | None) -> None:
        await self.db.execute(
            "INSERT INTO where_play (guild_id, channel_id, role_id, message_id) VALUES (?, ?, ?, NULL) "
            "ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id, "
            "role_id = EXCLUDED.role_id, message_id = NULL",
            (guild_id, channel_id, role_id),
        )

    async def set_message_id(self, guild_id: int, message_id: int) -> None:
        await self.db.execute("UPDATE where_play SET message_id = ? WHERE guild_id = ?", (message_id, guild_id))

    async def set_active(
        self,
        guild_id: int,
        *,
        code: str,
        team: str,
        caller_id: int,
        payload: str,
        fetched_at: datetime,
        active: bool = True,
    ) -> None:
        await self.db.execute(
            "UPDATE where_play SET code = ?, team = ?, caller_id = ?, payload = ?, fetched_at = ?, active = ? WHERE guild_id = ?",
            (code, team, caller_id, payload, fetched_at.isoformat(), int(active), guild_id),
        )

    async def store_payload(self, guild_id: int, payload: str, fetched_at: datetime) -> None:
        await self.db.execute(
            "UPDATE where_play SET payload = ?, fetched_at = ? WHERE guild_id = ?",
            (payload, fetched_at.isoformat(), guild_id),
        )

    async def get(self, guild_id: int) -> WherePlayRow | None:
        row = await self.db.fetchone("SELECT * FROM where_play WHERE guild_id = ?", (guild_id,))
        return cast(WherePlayRow, dict(row)) if row else None

    async def active_guilds(self) -> list[WherePlayRow]:
        rows = await self.db.fetchall("SELECT * FROM where_play WHERE active = 1 ORDER BY guild_id", ())
        return [cast(WherePlayRow, dict(row)) for row in rows]

    async def remember_caller_room(self, guild_id: int, member_id: int, channel_id: int, now: datetime) -> None:
        await self.db.execute(
            "INSERT INTO caller_rooms (guild_id, member_id, channel_id, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (guild_id, member_id) DO UPDATE SET channel_id = EXCLUDED.channel_id, "
            "updated_at = EXCLUDED.updated_at",
            (guild_id, member_id, channel_id, now.isoformat()),
        )

    async def forget_caller_room(self, guild_id: int, member_id: int) -> None:
        await self.db.execute("DELETE FROM caller_rooms WHERE guild_id = ? AND member_id = ?", (guild_id, member_id))

    async def caller_rooms(self, guild_id: int) -> list[CallerRoomRow]:
        rows = await self.db.fetchall(
            "SELECT guild_id, member_id, channel_id, updated_at FROM caller_rooms WHERE guild_id = ? ORDER BY member_id",
            (guild_id,),
        )
        return [cast(CallerRoomRow, dict(row)) for row in rows]
