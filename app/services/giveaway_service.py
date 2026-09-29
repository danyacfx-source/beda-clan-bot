"""Сервис розыгрышей."""

from __future__ import annotations

import secrets
from datetime import datetime

import discord

from app.core.base import BaseService
from app.db.giveaways_repository import GiveawaysRepository
from app.types import GiveawayRow


class GiveawayService(BaseService[GiveawaysRepository]):
    repo: GiveawaysRepository

    def __init__(self, repo: GiveawaysRepository) -> None:
        super().__init__(repo)

    async def create(
        self, guild_id: int, channel_id: int, author_id: int, prize: str, winners: int, ends_at: datetime, min_days: int = 0
    ) -> int:
        if winners < 1:
            raise ValueError("Минимум один победитель")
        if ends_at <= datetime.now(ends_at.tzinfo):
            raise ValueError("Дата окончания уже наступила")
        return await self._repo.create(guild_id, channel_id, author_id, prize, winners, ends_at, max(0, min_days))

    async def bind_message(self, giveaway_id: int, message_id: int) -> None:
        await self._repo.set_message_id(giveaway_id, message_id)

    async def get(self, giveaway_id: int) -> GiveawayRow | None:
        return await self._repo.get(giveaway_id)

    async def get_by_message(self, message_id: int) -> GiveawayRow | None:
        return await self._repo.get_by_message(message_id)

    async def expired(self, moment: datetime) -> list[GiveawayRow]:
        return await self._repo.active_expired(moment)

    async def active_with_message(self) -> list[GiveawayRow]:
        return await self._repo.active_with_message()

    async def recent_for_guild(self, guild_id: int, limit: int = 50) -> list[GiveawayRow]:
        return await self._repo.recent_for_guild(guild_id, limit)

    async def embed(self, giveaway: GiveawayRow) -> discord.Embed:
        from datetime import datetime

        from app.core import embeds
        from app.utils.format import relative

        entries = await self._repo.entries(giveaway["id"])
        embed = embeds.brand("Розыгрыш", f"## {giveaway['prize']}\nНажмите кнопку ниже, чтобы участвовать.")
        embed.add_field(name="УЧАСТНИКОВ", value=f"**{len(entries)}**", inline=True)
        embed.add_field(name="ПОБЕДИТЕЛЕЙ", value=f"**{giveaway['winners']}**", inline=True)
        embed.add_field(name="НОМЕР", value=f"`#{giveaway['id']}`", inline=True)
        min_days = giveaway.get("min_days", 0)
        if min_days > 0:
            embed.add_field(name="УСЛОВИЕ", value=f"От {min_days} дней на сервере", inline=False)
        if giveaway["active"]:
            embed.set_footer(text=f"Окончание: {relative(datetime.fromisoformat(giveaway['ends_at']))}")
        else:
            embed.set_footer(text="Розыгрыш завершён")
        return embed

    async def join(self, giveaway_id: int, user_id: int) -> bool:
        giveaway = await self._repo.get(giveaway_id)
        if giveaway is None or not giveaway["active"]:
            return False
        return await self._repo.add_entry(giveaway_id, user_id)

    async def entries(self, giveaway_id: int) -> list[int]:
        return await self._repo.entries(giveaway_id)

    async def finish(self, giveaway_id: int) -> None:
        await self._repo.end(giveaway_id)

    async def claim(self, giveaway_id: int, lease_seconds: int = 120) -> GiveawayRow | None:
        return await self._repo.claim(giveaway_id, lease_seconds)

    @staticmethod
    def draw(entries: list[int], winners: int) -> list[int]:
        if not entries:
            return []
        return secrets.SystemRandom().sample(entries, k=min(max(0, winners), len(entries)))
