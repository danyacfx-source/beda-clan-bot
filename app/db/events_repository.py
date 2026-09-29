"""Репозиторий ивентов (events, event_signup)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.db.base_repository import BaseRepository
from app.types import EventRow, EventSignupRow

if TYPE_CHECKING:
    from datetime import datetime

SIGNUP_ROLES = ("going_inf", "going_tech", "maybe", "sl", "camera", "not_going")


class EventsRepository(BaseRepository):
    async def create(
        self,
        *,
        guild_id: int,
        channel_id: int,
        name: str,
        event_type: str,
        description: str,
        briefing_at: datetime,
        start_at: datetime,
        image_url: str,
        show_not_going: bool,
        creator_id: int,
        created_at: datetime,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO events (guild_id, channel_id, name, event_type, description, briefing_at, "
            "start_at, image_url, show_not_going, creator_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                channel_id,
                name,
                event_type,
                description,
                briefing_at.isoformat(),
                start_at.isoformat(),
                image_url,
                int(show_not_going),
                creator_id,
                created_at.isoformat(),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID ивента")
        return int(cursor.lastrowid)

    async def set_message_id(self, event_id: int, message_id: int) -> None:
        await self.db.execute("UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id))

    async def get(self, event_id: int) -> EventRow | None:
        row = await self.db.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))
        return cast(EventRow, dict(row)) if row else None

    async def get_by_message(self, message_id: int) -> EventRow | None:
        row = await self.db.fetchone("SELECT * FROM events WHERE message_id = ?", (message_id,))
        return cast(EventRow, dict(row)) if row else None

    async def active_with_message(self) -> list[EventRow]:
        rows = await self.db.fetchall("SELECT * FROM events WHERE active = 1 AND message_id IS NOT NULL ORDER BY id", ())
        return [cast(EventRow, dict(row)) for row in rows]

    async def recent_for_guild(self, guild_id: int, limit: int = 25) -> list[EventRow]:
        rows = await self.db.fetchall("SELECT * FROM events WHERE guild_id = ? ORDER BY start_at DESC LIMIT ?", (guild_id, limit))
        return [cast(EventRow, dict(row)) for row in rows]

    async def update_fields(self, event_id: int, field: str, value: str) -> None:
        if field not in ("name", "description", "briefing_at", "start_at", "image_url"):
            raise ValueError(f"Поле ивента не редактируется: {field}")
        await self.db.execute(f"UPDATE events SET {field} = ? WHERE id = ?", (value, event_id))

    async def set_show_not_going(self, event_id: int, value: bool) -> None:
        await self.db.execute("UPDATE events SET show_not_going = ? WHERE id = ?", (int(value), event_id))

    async def cancel(self, event_id: int) -> None:
        await self.db.execute(
            "UPDATE events SET active = 0, reminded_briefing = 1, reminded_start = 1 WHERE id = ?",
            (event_id,),
        )

    async def set_signup(self, event_id: int, user_id: int, role: str, now: datetime) -> bool:
        """Ставит один статус участника; повторный выбор того же статуса снимает его."""
        if role not in SIGNUP_ROLES:
            raise ValueError(f"Неизвестный статус участия: {role}")
        existing = await self.db.fetchone("SELECT role FROM event_signup WHERE event_id = ? AND user_id = ?", (event_id, user_id))
        if existing is not None and str(existing["role"]) == role:
            await self.db.execute("DELETE FROM event_signup WHERE event_id = ? AND user_id = ?", (event_id, user_id))
            return False
        await self.db.execute(
            "INSERT INTO event_signup (event_id, user_id, role, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (event_id, user_id) DO UPDATE SET role = EXCLUDED.role, updated_at = EXCLUDED.updated_at",
            (event_id, user_id, role, now.isoformat()),
        )
        return True

    async def signups(self, event_id: int) -> list[EventSignupRow]:
        rows = await self.db.fetchall("SELECT user_id, role FROM event_signup WHERE event_id = ? ORDER BY user_id", (event_id,))
        return [cast(EventSignupRow, dict(row)) for row in rows]

    async def participants(self, event_id: int) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT user_id FROM event_signup WHERE event_id = ? AND role != 'not_going' ORDER BY user_id",
            (event_id,),
        )
        return [int(row["user_id"]) for row in rows]

    async def signup_counts(self, event_id: int) -> dict[str, list[int]]:
        rows = await self.db.fetchall("SELECT role, user_id FROM event_signup WHERE event_id = ? ORDER BY user_id", (event_id,))
        counts: dict[str, list[int]] = {role: [] for role in SIGNUP_ROLES}
        for row in rows:
            counts.setdefault(str(row["role"]), []).append(int(row["user_id"]))
        return counts

    async def due_reminders(self, now: datetime, lead: datetime) -> list[EventRow]:
        rows = await self.db.fetchall(
            "SELECT * FROM events WHERE active = 1 AND start_at >= ? "
            "AND ((reminded_briefing = 0 AND briefing_at <= ?) OR (reminded_start = 0 AND start_at <= ?)) "
            "ORDER BY start_at",
            (now.isoformat(), lead.isoformat(), lead.isoformat()),
        )
        return [cast(EventRow, dict(row)) for row in rows]

    async def mark_reminded(self, event_id: int, *, briefing: bool = False, start: bool = False) -> None:
        sets: list[str] = []
        params: list[int] = []
        if briefing:
            sets.append("reminded_briefing = 1")
        if start:
            sets.append("reminded_start = 1")
        if not sets:
            return
        params.append(event_id)
        await self.db.execute(f"UPDATE events SET {', '.join(sets)} WHERE id = ?", tuple(params))
