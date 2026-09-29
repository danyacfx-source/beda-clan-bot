"""Репозиторий розыгрышей (giveaways)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.base_repository import BaseRepository
from app.types import GiveawayRow

if TYPE_CHECKING:
    from datetime import datetime


class GiveawaysRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        channel_id: int,
        author_id: int,
        prize: str,
        winners: int,
        ends_at: datetime,
        min_days: int = 0,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO giveaways (guild_id, channel_id, author_id, prize, winners, ends_at, created_at, min_days) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, author_id, prize, winners, ends_at.isoformat(), _now_iso(), min_days),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID розыгрыша")
        return int(cursor.lastrowid)

    async def set_message_id(self, giveaway_id: int, message_id: int) -> None:
        await self.db.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id))

    async def get(self, giveaway_id: int) -> GiveawayRow | None:
        row = await self.db.fetchone("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
        return cast(GiveawayRow, dict(row)) if row else None

    async def get_by_message(self, message_id: int) -> GiveawayRow | None:
        row = await self.db.fetchone("SELECT * FROM giveaways WHERE message_id = ?", (message_id,))
        return cast(GiveawayRow, dict(row)) if row else None

    async def active_with_message(self) -> list[GiveawayRow]:
        rows = await self.db.fetchall("SELECT * FROM giveaways WHERE active = 1 AND message_id IS NOT NULL", ())
        return [cast(GiveawayRow, dict(row)) for row in rows]

    async def active_expired(self, moment: datetime) -> list[GiveawayRow]:
        rows = await self.db.fetchall("SELECT * FROM giveaways WHERE active = 1 AND ends_at <= ? ORDER BY ends_at", (moment.isoformat(),))
        return [cast(GiveawayRow, dict(row)) for row in rows]

    async def end(self, giveaway_id: int) -> None:
        await self.db.execute(
            "UPDATE giveaways SET active = 0, processing_until = NULL WHERE id = ?",
            (giveaway_id,),
        )

    async def claim(self, giveaway_id: int, lease_seconds: int = 120) -> GiveawayRow | None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=max(10, lease_seconds))
        row = await self.db.execute_returning(
            """
            UPDATE giveaways
            SET processing_until = ?
            WHERE id = ?
              AND active = 1
              AND (processing_until IS NULL OR processing_until <= ?)
            RETURNING *
            """,
            (lease_until.isoformat(), giveaway_id, now.isoformat()),
        )
        return cast(GiveawayRow, dict(row)) if row else None

    async def add_entry(self, giveaway_id: int, user_id: int) -> bool:
        cursor = await self.db.execute(
            "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
            (giveaway_id, user_id),
        )
        return cursor.rowcount > 0

    async def entries(self, giveaway_id: int) -> list[int]:
        rows = await self.db.fetchall("SELECT user_id FROM giveaway_entries WHERE giveaway_id = ? ORDER BY user_id", (giveaway_id,))
        return [int(row["user_id"]) for row in rows]

    async def recent_for_guild(self, guild_id: int, limit: int = 50) -> list[GiveawayRow]:
        rows = await self.db.fetchall("SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT ?", (guild_id, limit))
        return [cast(GiveawayRow, dict(row)) for row in rows]


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
