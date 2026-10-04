"""Планировщик: отложенные сообщения по расписанию (разовые и повторяющиеся)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from app.core.base import ClanCog
from app.services.scheduler_service import ScheduledMessagesService
from app.types import ScheduledMessageRow

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

# Пауза перед повторной попыткой доставки. Аренда строки при этом не
# снимается в NULL, а продлевается: иначе release_claim + claim_due в том же
# такте возвращали одну и ту же строку, и недоступный канал превращался в
# плотный цикл из 100 запросов к Discord API каждые 15 секунд.
_RETRY_BACKOFF: tuple[int, ...] = (60, 300, 900, 3600)


class SchedulerCog(ClanCog, name="Scheduler"):
    def __init__(self, bot: ClanBot, scheduled: ScheduledMessagesService) -> None:
        super().__init__(bot)
        self.scheduled = scheduled
        self._attempts: dict[int, int] = {}

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
                logger.exception("Не удалось получить claim запланированного сообщения")
                return
            if row is None:
                return
            try:
                await self._dispatch(row)
            except Exception:
                attempts = self._attempts.get(row["id"], 0) + 1
                self._attempts[row["id"]] = attempts
                delay = _RETRY_BACKOFF[min(attempts - 1, len(_RETRY_BACKOFF) - 1)]
                logger.exception(
                    "Ошибка при отправке запланированного сообщения #%s (попытка %s, повтор через %s сек)",
                    row["id"],
                    attempts,
                    delay,
                )
                await self.scheduled.defer_claim(row["id"], delay)
            else:
                self._attempts.pop(row["id"], None)

    @delivery_loop.before_loop
    async def before_delivery_loop(self) -> None:
        # Первый прогон tasks.loop случается в setup_hook — до READY, когда
        # кэш каналов пуст: get_channel давал None, и отложенное сообщение
        # молча помечалось выполненным.
        await self.bot.wait_until_ready()

    async def _dispatch(self, row: ScheduledMessageRow) -> None:
        channel = self.bot.get_channel(row["channel_id"])
        if channel is None:
            # Тред может не быть в кэше (архивированный / созданный до
            # старта). Запрос в API дешевле потери сообщения.
            try:
                channel = await self.bot.fetch_channel(row["channel_id"])
            except (discord.NotFound, discord.Forbidden):
                logger.warning(
                    "Запланированное сообщение #%s: канал %s недоступен — помечено выполненным",
                    row["id"],
                    row["channel_id"],
                )
                await self.scheduled.mark_done(row["id"])
                return
        # Thread — не подкласс TextChannel: сообщение, запланированное в треде,
        # иначе сбрасывалось без отправки.
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "Запланированное сообщение #%s: канал %s не текстовый — помечено выполненным",
                row["id"],
                row["channel_id"],
            )
            await self.scheduled.mark_done(row["id"])
            return
        embed = self.scheduled.build_embed(row)
        await channel.send(content=row["content"] or None, embed=embed)
        # Отправка прошла. mark_done выполняется отдельно: его сбой не должен
        # откатывать аренду — иначе сообщение ушло бы повторно.
        await self._mark_done_with_retry(row["id"])

    async def _mark_done_with_retry(self, scheduled_id: int) -> None:
        for attempt in range(3):
            try:
                await self.scheduled.mark_done(scheduled_id)
                return
            except Exception:
                if attempt == 2:
                    logger.exception(
                        "Сообщение #%s доставлено, но не помечено выполненным — возможен повтор при истечении аренды",
                        scheduled_id,
                    )
                else:
                    await asyncio.sleep(1)
