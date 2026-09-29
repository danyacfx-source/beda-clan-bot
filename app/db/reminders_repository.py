"""Репозиторий напоминаний."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.base_repository import BaseRepository
from app.types import ReminderRow, ReminderSummary

if TYPE_CHECKING:
    from datetime import datetime


class RemindersRepository(BaseRepository):
    async def add(self, user_id: int, guild_id: int | None, channel_id: int | None, message: str, remind_at: datetime) -> int:
        cursor = await self.db.execute(
            "INSERT INTO reminders (user_id, guild_id, channel_id, message, remind_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, guild_id, channel_id, message, remind_at.isoformat(), _now_iso()),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID напоминания")
        return int(cursor.lastrowid)

    async def active_for_user(self, user_id: int) -> list[ReminderSummary]:
        rows = await self.db.fetchall(
            "SELECT id, message, remind_at FROM reminders WHERE user_id = ? AND active = 1 ORDER BY remind_at",
            (user_id,),
        )
        return [cast(ReminderSummary, dict(row)) for row in rows]

    async def count_for_user(self, user_id: int) -> int:
        row = await self.db.fetchone("SELECT COUNT(*) AS total FROM reminders WHERE user_id = ? AND active = 1", (user_id,))
        return int(row["total"]) if row else 0

    async def due_up_to(self, moment: datetime) -> list[ReminderRow]:
        rows = await self.db.fetchall(
            "SELECT id, user_id, guild_id, channel_id, message, remind_at FROM reminders "
            "WHERE active = 1 AND remind_at <= ? ORDER BY remind_at",
            (moment.isoformat(),),
        )
        return [cast(ReminderRow, dict(row)) for row in rows]

    async def claim_due(self, moment: datetime, lease_seconds: int = 120) -> ReminderRow | None:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=max(10, lease_seconds))
        row = await self.db.execute_returning(
            """
            UPDATE reminders
            SET processing_until = ?
            WHERE id = (
                SELECT id FROM reminders
                WHERE active = 1
                  AND remind_at <= ?
                  AND (processing_until IS NULL OR processing_until <= ?)
                ORDER BY remind_at, id
                LIMIT 1
            )
            RETURNING id, user_id, guild_id, channel_id, message, remind_at
            """,
            (lease_until.isoformat(), moment.isoformat(), now.isoformat()),
        )
        return cast(ReminderRow, dict(row)) if row else None

    async def release_claim(self, reminder_id: int) -> None:
        await self.db.execute(
            "UPDATE reminders SET processing_until = NULL WHERE id = ? AND active = 1",
            (reminder_id,),
        )

    async def cancel(self, user_id: int, reminder_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
        return cursor.rowcount > 0

    async def cancel_all(self, user_id: int) -> int:
        cursor = await self.db.execute("DELETE FROM reminders WHERE user_id = ? AND active = 1", (user_id,))
        return cursor.rowcount

    async def deactivate(self, reminder_id: int) -> None:
        await self.db.execute(
            "UPDATE reminders SET active = 0, processing_until = NULL WHERE id = ?",
            (reminder_id,),
        )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
