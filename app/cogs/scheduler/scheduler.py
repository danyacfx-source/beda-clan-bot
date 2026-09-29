"""Планировщик: отложенные сообщения из вебпанели и команды форума."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from app.core.base import MegaCog
from app.services.scheduler_service import ScheduledMessagesService
from app.types import ScheduledMessageRow

if TYPE_CHECKING:
    from app.core.bot import MegaBot

logger = logging.getLogger("bot.cogs")


class SchedulerCog(MegaCog, name="Scheduler"):
    def __init__(self, bot: MegaBot, scheduled: ScheduledMessagesService) -> None:
        super().__init__(bot)
        self.scheduled = scheduled

    async def cog_load(self) -> None:
        self.delivery_loop.start()

    async def cog_unload(self) -> None:
        self.delivery_loop.cancel()

    @tasks.loop(seconds=15.0)
    async def delivery_loop(self) -> None:
        for _ in range(100):
            try:
                row = await self.scheduled.claim_due(datetime.now(UTC))
            except Exception:
                logger.exception("Ошибка при claim отложенного сообщения")
                return
            if row is None:
                return
            try:
                await self._dispatch(row)
            except Exception:
                logger.exception("Ошибка при отправке отложенного сообщения #%s", row["id"])
                await self.scheduled.release_claim(row["id"])

    async def _dispatch(self, row: ScheduledMessageRow) -> None:
        channel = self.bot.get_channel(row["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            await self.scheduled.mark_done(row["id"])
            return
        embed = self.scheduled.build_embed(row)
        await channel.send(content=row["content"] or None, embed=embed)
        await self.scheduled.mark_done(row["id"])
