"""Дни рождения участников: дата (месяц, день) одного человека."""

from __future__ import annotations

from typing import cast

from app.db.base_repository import BaseRepository
from app.types import BirthdayRow


class BirthdaysRepository(BaseRepository):
    async def set(self, user_id: int, month: int, day: int) -> None:
        await self.db.execute(
            "INSERT INTO birthdays (user_id, month, day) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET month = excluded.month, day = excluded.day",
            (user_id, month, day),
        )

    async def get(self, user_id: int) -> tuple[int, int] | None:
        row = await self.db.fetchone("SELECT month, day FROM birthdays WHERE user_id = ?", (user_id,))
        return (row["month"], row["day"]) if row else None

    async def remove(self, user_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM birthdays WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0

    async def all(self) -> list[BirthdayRow]:
        rows = await self.db.fetchall("SELECT user_id, month, day FROM birthdays ORDER BY month, day")
        return [cast(BirthdayRow, dict(row)) for row in rows]

    async def with_date(self, month: int, day: int) -> list[BirthdayRow]:
        rows = await self.db.fetchall("SELECT user_id, month, day FROM birthdays WHERE month = ? AND day = ?", (month, day))
        return [cast(BirthdayRow, dict(row)) for row in rows]
