"""Дни рождения: хранение даты и выборка по числу/месяцу."""

from __future__ import annotations

from app.core.base import BaseService
from app.db.birthdays_repository import BirthdaysRepository
from app.types import BirthdayRow


class BirthdayService(BaseService[BirthdaysRepository]):
    def __init__(self, repo: BirthdaysRepository) -> None:
        super().__init__(repo)

    async def set(self, user_id: int, month: int, day: int) -> None:
        await self._repo.set(user_id, month, day)

    async def get(self, user_id: int) -> tuple[int, int] | None:
        return await self._repo.get(user_id)

    async def remove(self, user_id: int) -> bool:
        return await self._repo.remove(user_id)

    async def all(self) -> list[BirthdayRow]:
        return await self._repo.all()

    async def with_date(self, month: int, day: int) -> list[BirthdayRow]:
        return await self._repo.with_date(month, day)
