"""Сервис напоминаний."""

from __future__ import annotations

from datetime import datetime

from app.core.base import BaseService
from app.db.reminders_repository import RemindersRepository
from app.types import ReminderRow, ReminderSummary


class ReminderService(BaseService[RemindersRepository]):
    repo: RemindersRepository

    def __init__(self, repo: RemindersRepository) -> None:
        super().__init__(repo)

    async def schedule(self, user_id: int, guild_id: int | None, channel_id: int | None, message: str, remind_at: datetime) -> int:
        if remind_at <= datetime.now(remind_at.tzinfo):
            raise ValueError("Время уже наступило")
        return await self._repo.add(user_id, guild_id, channel_id, message, remind_at)

    async def due_up_to(self, moment: datetime) -> list[ReminderRow]:
        return await self._repo.due_up_to(moment)

    async def claim_due(self, moment: datetime, lease_seconds: int = 120) -> ReminderRow | None:
        return await self._repo.claim_due(moment, lease_seconds)

    async def release_claim(self, reminder_id: int) -> None:
        await self._repo.release_claim(reminder_id)

    async def defer_claim(self, reminder_id: int, seconds: int) -> None:
        await self._repo.defer_claim(reminder_id, seconds)

    async def cancel(self, user_id: int, reminder_id: int) -> bool:
        return await self._repo.cancel(user_id, reminder_id)

    async def cancel_all(self, user_id: int) -> int:
        return await self._repo.cancel_all(user_id)

    async def active_for_user(self, user_id: int) -> list[ReminderSummary]:
        return await self._repo.active_for_user(user_id)

    async def count_for_user(self, user_id: int) -> int:
        return await self._repo.count_for_user(user_id)

    async def mark_done(self, reminder_id: int) -> None:
        await self._repo.deactivate(reminder_id)
