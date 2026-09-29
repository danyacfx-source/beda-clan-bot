"""Репозиторий опросов."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.db.base_repository import BaseRepository

if TYPE_CHECKING:
    pass


class PollsRepository(BaseRepository):
    async def create(self, guild_id: int, channel_id: int, author_id: int, question: str, options: list[str]) -> int:
        cursor = await self.db.execute(
            "INSERT INTO polls (guild_id, channel_id, author_id, question, options, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, author_id, question, json.dumps(options, ensure_ascii=False), _now_iso()),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID опроса")
        return int(cursor.lastrowid)

    async def set_message_id(self, poll_id: int, message_id: int) -> None:
        await self.db.execute("UPDATE polls SET message_id = ? WHERE id = ?", (message_id, poll_id))

    async def get_by_message(self, message_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM polls WHERE message_id = ?", (message_id,))
        return dict(row) if row else None

    async def get(self, poll_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM polls WHERE id = ?", (poll_id,))
        return dict(row) if row else None

    async def end(self, poll_id: int) -> None:
        await self.db.execute("UPDATE polls SET active = 0 WHERE id = ?", (poll_id,))

    async def active_with_message(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM polls WHERE active = 1 AND message_id IS NOT NULL", ())
        return [dict(row) for row in rows]

    async def list_for_guild(self, guild_id: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM polls WHERE guild_id = ? ORDER BY id DESC LIMIT ?", (guild_id, limit))
        return [dict(row) for row in rows]

    async def cast_vote(self, poll_id: int, user_id: int, option: int) -> int:
        """Возвращает 1 если голос изменён, 2 если новый, 0 если тот же."""
        current = await self.db.fetchone("SELECT option FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))
        if current is not None and int(current["option"]) == option:
            return 0
        await self.db.execute(
            "INSERT OR REPLACE INTO poll_votes (poll_id, user_id, option) VALUES (?, ?, ?)",
            (poll_id, user_id, option),
        )
        return 1 if current is not None else 2

    async def vote_counts(self, poll_id: int) -> dict[int, int]:
        rows = await self.db.fetchall("SELECT option, COUNT(*) AS total FROM poll_votes WHERE poll_id = ? GROUP BY option", (poll_id,))
        return {int(row["option"]): int(row["total"]) for row in rows}


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
