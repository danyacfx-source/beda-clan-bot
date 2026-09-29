"""Репозиторий единых moderation cases."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from app.db.base_repository import BaseRepository
from app.types import ModerationCaseRow


class ModerationCasesRepository(BaseRepository):
    async def create(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        created_at: datetime,
        expires_at: datetime | None = None,
    ) -> int:
        cursor = await self.db.execute(
            """
            INSERT INTO moderation_cases
                (guild_id, user_id, moderator_id, action, reason, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                moderator_id,
                action,
                reason[:1000],
                created_at.isoformat(),
                expires_at.isoformat() if expires_at else None,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID moderation case")
        return int(cursor.lastrowid)

    async def get(self, guild_id: int, case_id: int) -> ModerationCaseRow | None:
        row = await self.db.fetchone(
            "SELECT * FROM moderation_cases WHERE guild_id = ? AND case_id = ?",
            (guild_id, case_id),
        )
        return cast(ModerationCaseRow, dict(row)) if row else None

    async def list_for_guild(self, guild_id: int, limit: int = 100) -> list[ModerationCaseRow]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM moderation_cases
            WHERE guild_id = ?
            ORDER BY case_id DESC
            LIMIT ?
            """,
            (guild_id, max(1, min(limit, 500))),
        )
        return [cast(ModerationCaseRow, dict(row)) for row in rows]

    async def close(self, guild_id: int, case_id: int) -> bool:
        cursor = await self.db.execute(
            "UPDATE moderation_cases SET active = 0 WHERE guild_id = ? AND case_id = ? AND active = 1",
            (guild_id, case_id),
        )
        return cursor.rowcount > 0
