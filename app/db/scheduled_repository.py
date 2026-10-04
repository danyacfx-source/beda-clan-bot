"""Репозиторий отложенных сообщений (планировщик)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.base_repository import BaseRepository
from app.types import ScheduledMessageRow

if TYPE_CHECKING:
    from datetime import datetime


class ScheduledRepository(BaseRepository):
    async def create(self, guild_id: int, channel_id: int, author_id: int, content: str, embed_json: str, send_at: datetime) -> int:
        cursor = await self.db.execute(
            "INSERT INTO scheduled_messages (guild_id, channel_id, author_id, content, embed_json, send_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, author_id, content, embed_json, send_at.isoformat(), _now_iso()),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID запланированного сообщения")
        return int(cursor.lastrowid)

    async def get(self, scheduled_id: int) -> ScheduledMessageRow | None:
        row = await self.db.fetchone("SELECT * FROM scheduled_messages WHERE id = ?", (scheduled_id,))
        return cast(ScheduledMessageRow, dict(row)) if row else None

    async def due_up_to(self, moment: datetime) -> list[ScheduledMessageRow]:
        rows = await self.db.fetchall(
            "SELECT * FROM scheduled_messages WHERE done = 0 AND send_at <= ? ORDER BY send_at", (moment.isoformat(),)
        )
        return [cast(ScheduledMessageRow, dict(row)) for row in rows]

    async def claim_due(self, moment: datetime, lease_seconds: int = 120) -> ScheduledMessageRow | None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=max(10, lease_seconds))
        row = await self.db.execute_returning(
            """
            UPDATE scheduled_messages
            SET processing_until = ?
            WHERE id = (
                SELECT id FROM scheduled_messages
                WHERE done = 0
                  AND send_at <= ?
                  AND (processing_until IS NULL OR processing_until <= ?)
                ORDER BY send_at, id
                LIMIT 1
            )
            RETURNING *
            """,
            (lease_until.isoformat(), moment.isoformat(), now.isoformat()),
        )
        return cast(ScheduledMessageRow, dict(row)) if row else None

    async def release_claim(self, scheduled_id: int) -> None:
        await self.db.execute(
            "UPDATE scheduled_messages SET processing_until = NULL WHERE id = ? AND done = 0",
            (scheduled_id,),
        )

    async def defer_claim(self, scheduled_id: int, seconds: int) -> None:
        """Продлевает аренду вместо её сброса: повторная попытка доставки
        произойдёт через ``seconds``, а не сразу в этом же такте."""
        from datetime import UTC, datetime, timedelta

        until = datetime.now(UTC) + timedelta(seconds=max(1, seconds))
        await self.db.execute(
            "UPDATE scheduled_messages SET processing_until = ? WHERE id = ? AND done = 0",
            (until.isoformat(), scheduled_id),
        )

    async def upcoming(self, limit: int = 100) -> list[ScheduledMessageRow]:
        rows = await self.db.fetchall("SELECT * FROM scheduled_messages WHERE done = 0 ORDER BY send_at LIMIT ?", (limit,))
        return [cast(ScheduledMessageRow, dict(row)) for row in rows]

    async def recent(self, limit: int = 100) -> list[ScheduledMessageRow]:
        rows = await self.db.fetchall("SELECT * FROM scheduled_messages ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [cast(ScheduledMessageRow, dict(row)) for row in rows]

    async def delete(self, scheduled_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM scheduled_messages WHERE id = ?", (scheduled_id,))
        return cursor.rowcount > 0

    async def mark_done(self, scheduled_id: int) -> None:
        await self.db.execute(
            "UPDATE scheduled_messages SET done = 1, processing_until = NULL WHERE id = ?",
            (scheduled_id,),
        )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
