"""Дни рождения: команды /birthday и ежедневный анонс именинников."""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.services.birthday_service import BirthdayService

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

DATE_RE = re.compile(r"^\s*(\d{1,2})[./\\-](\d{1,2})\s*$")


class BirthdaysCog(ClanCog, name="Birthdays"):
    birthday = app_commands.Group(name="birthday", description="Дни рождения участников")

    def __init__(self, bot: ClanBot, birthdays: BirthdayService) -> None:
        super().__init__(bot)
        self.birthdays = birthdays
        self._task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        # Цикл запускаем всегда: настройку канала можно задать из панели уже
        # после старта, а условие «канал задан» на момент загрузки кога
        # оставляло анонс выключенным до рестарта.
        if self._task is None:
            self._task = self.bot.loop.create_task(self._loop())

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                # Час читаем каждый круг: смена настройки в панели подхватывается без рестарта.
                conf = await self.module_config(self.bot.config.guild_id, "birthdays")
                # Значение из БД не валидируется на запись: некорректный час
                # убивал бы весь цикл на datetime.replace(hour=...).
                hour = max(0, min(23, int(conf["announce_hour"] or 0)))
                now = datetime.now()
                target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())
                await self._announce()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Birthdays: ошибка цикла анонса")
                await asyncio.sleep(60)

    async def _announce(self) -> None:
        conf = await self.module_config(self.bot.config.guild_id, "birthdays")
        channel_id = conf["channel_id"]
        if channel_id is None:
            return
        channel = self.bot.get_channel(channel_id)
        # В настройку может попасть голосовой канал или тред: у них нет send().
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning("Birthdays: канал %s недоступен или не текстовый — анонс пропущен", channel_id)
            return
        now = datetime.now()
        rows = await self.birthdays.with_date(now.month, now.day)
        if not rows:
            return
        guild = channel.guild
        lines = []
        for row in rows:
            display = None
            member = guild.get_member(row["user_id"])
            if member is not None:
                display = member.display_name
            mention = f"<@{row['user_id']}>"
            lines.append(f"🎂 **{display or mention}** {mention}")
        embed = embeds.brand(
            "Сегодня день рождения",
            "## Время поздравлять! 🎂\n\n" + "\n".join(lines),
            footer=f"BEDA  •  именинников сегодня: {len(lines)}",
        )
        content = None
        role_id = conf["ping_role_id"]
        if role_id is not None and guild.get_role(role_id) is not None:
            content = f"<@&{role_id}>"
        try:
            await channel.send(content=content, embed=embed)
            logger.info("Birthdays: анонс отправлен (%d участников)", len(lines))
        except discord.HTTPException:
            logger.warning("Birthdays: не удалось отправить анонс")

    @birthday.command(name="set", description="Указать свою дату рождения (дд.мм)")
    @app_commands.describe(date="Дата в формате дд.мм")
    @app_commands.guild_only()
    async def set_birthday(self, interaction: discord.Interaction, date: str) -> None:
        match = DATE_RE.match(date)
        if match is None:
            await interaction.response.send_message(
                embed=embeds.error("Неверный формат", "Укажите дату как `15.03` — день и месяц."),
                ephemeral=True,
            )
            return
        day, month = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 1 <= day <= calendar.monthrange(2000, month)[1]):
            await interaction.response.send_message(embed=embeds.error("Такой даты не существует"), ephemeral=True)
            return
        await self.birthdays.set(interaction.user.id, month, day)
        await interaction.response.send_message(
            embed=embeds.success(
                "Дата сохранена",
                f"**{day:02d}.{month:02d}** · в этот день бот напомнит серверу о празднике.",
            ),
            ephemeral=True,
        )

    @birthday.command(name="remove", description="Удалить свою дату рождения")
    @app_commands.guild_only()
    async def remove_birthday(self, interaction: discord.Interaction) -> None:
        if await self.birthdays.get(interaction.user.id) is None:
            await interaction.response.send_message(embed=embeds.info("Дата не установлена"), ephemeral=True)
            return
        await self.birthdays.remove(interaction.user.id)
        await interaction.response.send_message(embed=embeds.success("Дата удалена"), ephemeral=True)

    @birthday.command(name="list", description="Ближайшие дни рождения")
    @app_commands.guild_only()
    async def list_birthdays(self, interaction: discord.Interaction) -> None:
        rows = await self.birthdays.all()
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info("Календарь пока пуст", "Добавьте дату командой `/birthday set`."), ephemeral=True
            )
            return
        now = datetime.now()
        upcoming = []
        for row in rows:
            delta = self._days_until(now, row["month"], row["day"])
            upcoming.append((delta, row["user_id"], row["month"], row["day"]))
        upcoming.sort(key=lambda item: item[0])
        lines = []
        for delta, user_id, month, day in upcoming:
            if delta >= 365:
                continue
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member is not None else f"Пользователь {user_id}"
            when = "Сегодня! 🎉" if delta == 0 else ("Завтра" if delta == 1 else f"через {delta} дн.")
            lines.append(f"**{name}** — {day:02d}.{month:02d} ({when})")
        if not lines:
            await interaction.response.send_message(embed=embeds.info("Ближайших дней рождения нет"), ephemeral=True)
            return
        embed = embeds.brand(
            "Календарь дней рождения",
            "\n".join(f"`{index:02d}`  {line}" for index, line in enumerate(lines[:25], 1)),
            footer=f"BEDA  •  ближайшие события: {min(len(lines), 25)}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staticmethod
    def _days_until(now: datetime, month: int, day: int) -> int:
        try:
            this_year = datetime(now.year, month, day)
        except ValueError:
            this_year = datetime(now.year, month, 28)
        delta = (this_year - now).days
        if delta < 0:
            try:
                next_year = datetime(now.year + 1, month, day)
            except ValueError:
                next_year = datetime(now.year + 1, month, 28)
            delta = (next_year - now).days
        return delta
