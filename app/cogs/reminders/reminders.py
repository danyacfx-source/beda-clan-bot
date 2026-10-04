"""Напоминания: личные напоминания по времени с фоновой доставкой."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import tasks

from app.core import embeds
from app.core.base import ClanCog
from app.services.reminder_service import ReminderService
from app.types import ReminderRow
from app.utils.format import plural, relative
from app.utils.time import parse_duration

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

_MAX_SECONDS = 30 * 24 * 3600
_MAX_REMINDERS = 20

# Пауза перед повторной попыткой доставки (аренда строки продлевается, а не
# сбрасывается — иначе release_claim возвращал ту же строку в этом же такте).
_RETRY_BACKOFF: tuple[int, ...] = (60, 300, 900, 3600)


class RemindersCog(ClanCog, name="Reminders"):
    def __init__(self, bot: ClanBot, reminders: ReminderService) -> None:
        super().__init__(bot)
        self.reminders = reminders
        self._attempts: dict[int, int] = {}

    async def cog_load(self) -> None:
        self.check_loop.start()

    async def cog_unload(self) -> None:
        self.check_loop.cancel()

    @tasks.loop(seconds=30.0)
    async def check_loop(self) -> None:
        for _ in range(100):
            try:
                item = await self.reminders.claim_due(datetime.now(UTC))
            except Exception:
                logger.exception("Не удалось получить claim напоминания")
                return
            if item is None:
                return
            try:
                await self._deliver(item)
            except Exception:
                attempts = self._attempts.get(item["id"], 0) + 1
                self._attempts[item["id"]] = attempts
                delay = _RETRY_BACKOFF[min(attempts - 1, len(_RETRY_BACKOFF) - 1)]
                logger.exception(
                    "Не удалось доставить напоминание #%s (попытка %s, повтор через %s сек)",
                    item["id"],
                    attempts,
                    delay,
                )
                await self.reminders.defer_claim(item["id"], delay)
            else:
                self._attempts.pop(item["id"], None)
                await self._mark_done_with_retry(item["id"])

    @check_loop.before_loop
    async def before_check_loop(self) -> None:
        # Первый прогон tasks.loop случается в setup_hook, до READY: кэш пуст,
        # get_user/get_channel давали None, и просроченное напоминание молча
        # деактивировалось.
        await self.bot.wait_until_ready()

    async def _mark_done_with_retry(self, reminder_id: int) -> None:
        """Доставка прошла: mark_done выполняется отдельно и с ретраем.

        Сбой mark_done не должен откатывать аренду (release_claim) — иначе
        напоминание ушло бы повторно.
        """
        for attempt in range(3):
            try:
                await self.reminders.mark_done(reminder_id)
                return
            except Exception:
                if attempt == 2:
                    logger.exception("Напоминание #%s доставлено, но не помечено выполненным", reminder_id)
                else:
                    await asyncio.sleep(1)

    async def _deliver(self, item: ReminderRow) -> None:
        embed = embeds.info("⏰ Напоминание", item["message"])
        embed.set_footer(text=f"ID напоминания: {item['id']}")
        channel = self.bot.get_channel(item["channel_id"]) if item["channel_id"] else None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            await channel.send(embed=embed)
            return

        user = self.bot.get_user(item["user_id"])
        if user is None:
            try:
                user = await self.bot.fetch_user(item["user_id"])
            except discord.HTTPException:
                user = None
        if user is None:
            logger.warning("Напоминание #%s: пользователь %s не найден — доставка пропущена", item["id"], item["user_id"])
            return
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            # У участника закрыты ЛС или он заблокировал бота: повторять
            # бессмысленно, но и помечать «выполнено» молча нельзя — пишем.
            logger.warning("Напоминание #%s: %s закрыл(а) ЛС — доставка пропущена", item["id"], user.id)
        except discord.HTTPException:
            # Временный сбой (429/5xx): пробрасываем, чтобы сработал ретрай
            # с откладыванием, а не потеря напоминания.
            logger.warning("Напоминание #%s: не удалось отправить ЛС пользователю %s", item["id"], user.id)
            raise

    @app_commands.command(name="remindme", description="Напомнить вам о чём-либо через некоторое время")
    @app_commands.describe(duration="Срок, например: 30s, 5m, 2h, 1d", text="Текст напоминания")
    async def remindme(self, interaction: discord.Interaction, duration: str, text: str) -> None:
        seconds = parse_duration(duration)
        if seconds is None or seconds <= 0:
            await interaction.response.send_message(embed=embeds.error("Ошибка формата", "Пример: `5m`, `2h`, `1d 30m`."), ephemeral=True)
            return
        if seconds > _MAX_SECONDS:
            await interaction.response.send_message(embed=embeds.error("Слишком долго", "Максимум — 30 дней."), ephemeral=True)
            return
        if len(text) > 1000:
            await interaction.response.send_message(embed=embeds.error("Слишком длинно", "Текст до 1000 символов."), ephemeral=True)
            return

        count = await self.reminders.count_for_user(interaction.user.id)
        if count >= _MAX_REMINDERS:
            await interaction.response.send_message(
                embed=embeds.error("Многовато", "Не более 20 активных напоминаний. Освободите место командой `/remind clear`."),
                ephemeral=True,
            )
            return

        remind_at = datetime.now(UTC) + timedelta(seconds=seconds)
        channel = None
        if isinstance(interaction.channel, discord.TextChannel):
            channel = interaction.channel
        reminder_id = await self.reminders.schedule(
            interaction.user.id,
            interaction.guild.id if interaction.guild else None,
            channel.id if channel else None,
            text,
            remind_at,
        )
        embed = embeds.success("Напоминание создано", text)
        embed.add_field(name="Напомнить", value=relative(remind_at), inline=True)
        embed.set_footer(text=f"ID: {reminder_id} • Место: {channel.mention if channel else 'Личные сообщения'}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remind", description="Список ваших напоминаний")
    async def remind_list(self, interaction: discord.Interaction) -> None:
        items = await self.reminders.active_for_user(interaction.user.id)
        if not items:
            await interaction.response.send_message(embed=embeds.info("Напоминаний нет", "Создайте через `/remindme`."), ephemeral=True)
            return
        embed = embeds.info(f"Ваши напоминания ({len(items)})")
        for item in items[:10]:
            target = datetime.fromisoformat(item["remind_at"])
            embed.add_field(name=f"#{item['id']}", value=f"{item['message'][:80]} — {relative(target)}", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remind_cancel", description="Отменить конкретное напоминание")
    @app_commands.describe(reminder_id="ID из списка /remind")
    async def remind_cancel(self, interaction: discord.Interaction, reminder_id: int) -> None:
        if await self.reminders.cancel(interaction.user.id, reminder_id):
            embed = embeds.success("Удалено", f"Напоминание `#{reminder_id}` отменено.")
        else:
            embed = embeds.error("Не найдено", "У вас нет такого напоминания.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="remind_clear", description="Удалить все ваши напоминания")
    async def remind_clear(self, interaction: discord.Interaction) -> None:
        count = await self.reminders.count_for_user(interaction.user.id)
        if count == 0:
            await interaction.response.send_message(embed=embeds.info("Нечего чистить"), ephemeral=True)
            return
        await self.reminders.cancel_all(interaction.user.id)
        words = plural(count, "штука", "штуки", "штук")
        await interaction.response.send_message(embed=embeds.success("Готово", f"Удалено напоминаний: **{count}** {words}."))
