"""Единая история модерационных действий."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.moderation_cases_repository import ModerationCasesRepository
from app.types import ModerationCaseRow


class ModerationCaseService:
    def __init__(self, repo: ModerationCasesRepository) -> None:
        self._repo = repo

    async def create(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str = "",
        expires_at: datetime | None = None,
    ) -> int:
        return await self._repo.create(
            guild_id,
            user_id,
            moderator_id,
            action,
            reason,
            datetime.now(UTC),
            expires_at,
        )

    async def get(self, guild_id: int, case_id: int) -> ModerationCaseRow | None:
        return await self._repo.get(guild_id, case_id)

    async def list_for_guild(self, guild_id: int, limit: int = 100) -> list[ModerationCaseRow]:
        return await self._repo.list_for_guild(guild_id, limit)

    async def close(self, guild_id: int, case_id: int) -> bool:
        return await self._repo.close(guild_id, case_id)
