"""Репозиторий отпусков и настроек системы отпусков (даты хранятся как ``YYYY-MM-DD``).

Отпуска не удаляются: отмена и завершение меняют ``status`` (``cancelled`` / ``ended``),
а читающие запросы учитывают только ``status = 'active'``.
"""

from __future__ import annotations

from typing import cast

from app.db.base_repository import BaseRepository
from app.types import VacationRow, VacationSettingsRow

STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"
STATUS_ENDED = "ended"


class VacationsRepository(BaseRepository):
    async def insert(
        self,
        guild_id: int,
        user_id: int,
        start_date: str,
        end_date: str,
        reason: str,
        created_at: str,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO vacations (guild_id, user_id, start_date, end_date, reason, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, start_date, end_date, reason, created_at, STATUS_ACTIVE),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("БД не вернула ID отпуска")
        return int(cursor.lastrowid)

    async def get(self, vacation_id: int) -> VacationRow | None:
        row = await self.db.fetchone("SELECT * FROM vacations WHERE id = ?", (vacation_id,))
        return cast(VacationRow, dict(row)) if row else None

    async def update_end(self, vacation_id: int, end_date: str, updated_at: str) -> None:
        await self.db.execute(
            "UPDATE vacations SET end_date = ?, updated_at = ? WHERE id = ?",
            (end_date, updated_at, vacation_id),
        )

    async def set_status(self, vacation_id: int, status: str, updated_at: str) -> None:
        await self.db.execute(
            "UPDATE vacations SET status = ?, updated_at = ? WHERE id = ?",
            (status, updated_at, vacation_id),
        )

    async def active_ended_before(self, today: str) -> list[VacationRow]:
        """Активные отпуска, у которых дата окончания уже прошла (до ``today``)."""
        rows = await self.db.fetchall(
            "SELECT * FROM vacations WHERE status = ? AND end_date < ? ORDER BY guild_id, id",
            (STATUS_ACTIVE, today),
        )
        return [cast(VacationRow, dict(row)) for row in rows]

    async def mark_ended_before(self, today: str, updated_at: str) -> int:
        cursor = await self.db.execute(
            "UPDATE vacations SET status = ?, updated_at = ? WHERE status = ? AND end_date < ?",
            (STATUS_ENDED, updated_at, STATUS_ACTIVE, today),
        )
        return cursor.rowcount

    async def for_user(self, guild_id: int, user_id: int) -> list[VacationRow]:
        """Активные отпуска участника (включая уже прошедшие, пока их не отметила очистка)."""
        rows = await self.db.fetchall(
            "SELECT * FROM vacations WHERE guild_id = ? AND user_id = ? AND status = ? ORDER BY start_date, id",
            (guild_id, user_id, STATUS_ACTIVE),
        )
        return [cast(VacationRow, dict(row)) for row in rows]

    async def for_guild(self, guild_id: int) -> list[VacationRow]:
        """Активные отпуска гильдии (для информационного сообщения)."""
        rows = await self.db.fetchall(
            "SELECT * FROM vacations WHERE guild_id = ? AND status = ? ORDER BY start_date, id",
            (guild_id, STATUS_ACTIVE),
        )
        return [cast(VacationRow, dict(row)) for row in rows]

    async def get_settings(self, guild_id: int) -> VacationSettingsRow | None:
        row = await self.db.fetchone("SELECT * FROM vacation_settings WHERE guild_id = ?", (guild_id,))
        return cast(VacationSettingsRow, dict(row)) if row else None

    async def all_settings(self) -> list[VacationSettingsRow]:
        rows = await self.db.fetchall("SELECT * FROM vacation_settings ORDER BY guild_id", ())
        return [cast(VacationSettingsRow, dict(row)) for row in rows]

    async def save_settings(self, guild_id: int, channel_id: int, info_message_id: int, panel_message_id: int) -> None:
        await self.db.execute(
            "INSERT INTO vacation_settings (guild_id, channel_id, info_message_id, panel_message_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id, "
            "info_message_id = EXCLUDED.info_message_id, panel_message_id = EXCLUDED.panel_message_id",
            (guild_id, channel_id, info_message_id, panel_message_id),
        )

    async def set_info_message(self, guild_id: int, message_id: int | None) -> None:
        await self.db.execute("UPDATE vacation_settings SET info_message_id = ? WHERE guild_id = ?", (message_id, guild_id))

    async def set_panel_message(self, guild_id: int, message_id: int | None) -> None:
        await self.db.execute("UPDATE vacation_settings SET panel_message_id = ? WHERE guild_id = ?", (message_id, guild_id))

    async def clear_messages(self, guild_id: int) -> None:
        """Сбрасывает ссылки на информационное сообщение и панель (канал недоступен)."""
        await self.db.execute(
            "UPDATE vacation_settings SET info_message_id = NULL, panel_message_id = NULL WHERE guild_id = ?",
            (guild_id,),
        )
