"""Розыгрыши: запуск по таймеру, участие кнопкой, победители."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import tasks

from app.core import embeds
from app.core.base import ClanCog
from app.core.views import GiveawayView
from app.services.giveaway_service import GiveawayService
from app.types import GiveawayRow
from app.utils.format import relative
from app.utils.time import parse_duration

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

_MAX_DURATION = 30 * 24 * 3600


class GiveawaysCog(ClanCog, name="Giveaways"):
    def __init__(self, bot: ClanBot, giveaways: GiveawayService) -> None:
        super().__init__(bot)
        self.giveaways = giveaways

    async def cog_load(self) -> None:
        self.check_loop.start()

    async def cog_unload(self) -> None:
        self.check_loop.cancel()

    @tasks.loop(seconds=20.0)
    async def check_loop(self) -> None:
        try:
            expired = await self.giveaways.expired(datetime.now(UTC))
        except Exception:
            logger.exception("Ошибка при выборке завершённых розыгрышей")
            return
        for giveaway in expired:
            try:
                await self._finish(giveaway)
            except Exception:
                logger.exception("Ошибка при завершении розыгрыша #%s", giveaway["id"])

    async def _finish(self, giveaway: GiveawayRow, *, reroll: bool = False, claim: bool = True) -> bool:
        if claim:
            claimed = await self.giveaways.claim(giveaway["id"])
            if claimed is None:
                return False
            giveaway = claimed
        entries = await self.giveaways.entries(giveaway["id"])
        winners_count = int(giveaway["winners"])
        winners = self.giveaways.draw(entries, winners_count)
        await self.giveaways.finish(giveaway["id"])

        channel = self.bot.get_channel(giveaway["channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return True

        header = "Перерозыгрыш" if reroll else "Приз"
        announce = embeds.success("🎉 Розыгрыш завершён", f"{header}: **{giveaway['prize']}**")
        if winners:
            mentions = ", ".join(f"<@{uid}>" for uid in winners)
            announce.add_field(name="Победители", value=mentions, inline=False)
        else:
            announce.add_field(name="Победители", value="Недостаточно участников", inline=False)
        await channel.send(embed=announce)

        if giveaway["message_id"]:
            try:
                message = await channel.fetch_message(giveaway["message_id"])
                embed = await self.giveaways.embed(giveaway)
                embed.set_footer(text="Розыгрыш завершён")
                await message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass
        return True

    async def _start_giveaway(
        self,
        interaction: discord.Interaction,
        duration_str: str,
        prize: str,
        winners: int = 1,
        min_days: int = 0,
        description: str | None = None,
    ) -> None:
        seconds: int | None
        if duration_str.isdigit():
            seconds = int(duration_str) * 60
        else:
            seconds = parse_duration(duration_str)
        if seconds is None or seconds <= 0:
            await interaction.response.send_message(
                embed=embeds.error("Ошибка формата", "Пример: `10m`, `1h`, `1d` или число минут."), ephemeral=True
            )
            return
        if seconds > _MAX_DURATION:
            await interaction.response.send_message(embed=embeds.error("Слишком долго", "Максимум — 30 дней."), ephemeral=True)
            return
        if not (1 <= winners <= 20):
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Победителей: от 1 до 20."), ephemeral=True)
            return
        if not prize.strip():
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Укажите приз."), ephemeral=True)
            return

        final_prize = f"{prize.strip()} — {description.strip()}" if description and description.strip() else prize.strip()

        ends_at = datetime.now(UTC) + timedelta(seconds=seconds)
        giveaway_id = await self.giveaways.create(
            interaction.guild.id, interaction.channel.id, interaction.user.id, final_prize[:256], winners, ends_at, max(0, min_days)
        )
        giveaway = await self.giveaways.get(giveaway_id)
        if giveaway is None:
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Розыгрыш создан, но его данные не удалось загрузить."),
                ephemeral=True,
            )
            return
        embed = await self.giveaways.embed(giveaway)
        view = GiveawayView()
        try:
            await interaction.response.send_message(embed=embed, view=view)
            message = await interaction.original_response()
            await self.giveaways.bind_message(giveaway_id, message.id)
            self.bot.add_view(view, message_id=message.id)
        except Exception:
            await self.giveaways.finish(giveaway_id)
            raise

    @app_commands.command(name="giveaway", description="Создать розыгрыш")
    @app_commands.describe(
        prize="Что разыгрываем",
        duration="Длительность (например 10m или минуты числом)",
        description="Описание или подробности",
        winners="Сколько победителей",
        min_days="Минимум дней на сервере",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def giveaway_cmd(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        description: str | None = None,
        winners: int = 1,
        min_days: int = 0,
    ) -> None:
        await self._start_giveaway(interaction, duration, prize, winners, min_days, description)

    @app_commands.command(name="gend", description="Завершить розыгрыш досрочно")
    @app_commands.describe(message_id="ID сообщения с розыгрышем")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def g_end(self, interaction: discord.Interaction, message_id: int) -> None:
        giveaway = await self.giveaways.get_by_message(message_id)
        if giveaway is None:
            await interaction.response.send_message(
                embed=embeds.error("Не найдено", "Розыгрыш не найден. Права изменять есть?"), ephemeral=True
            )
            return
        if giveaway["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                embed=embeds.error("Недоступно", "Этот розыгрыш принадлежит другому серверу."),
                ephemeral=True,
            )
            return
        if not giveaway["active"]:
            await interaction.response.send_message(embed=embeds.warning("Уже завершён", "Этот розыгрыш уже закрыт."), ephemeral=True)
            return
        if not await self._finish(giveaway):
            await interaction.response.send_message(
                embed=embeds.warning("Уже обрабатывается", "Этот розыгрыш уже завершает другой процесс."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embeds.success("Розыгрыш завершён", "Победители объявлены в канале."), ephemeral=True)

    async def _do_reroll(self, interaction: discord.Interaction, message_id: int) -> None:
        giveaway = await self.giveaways.get_by_message(message_id)
        if giveaway is None:
            await interaction.response.send_message(embed=embeds.error("Не найдено", "Розыгрыш не найден."), ephemeral=True)
            return
        if giveaway["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                embed=embeds.error("Недоступно", "Этот розыгрыш принадлежит другому серверу."),
                ephemeral=True,
            )
            return
        if giveaway["active"]:
            await interaction.response.send_message(
                embed=embeds.warning("Сначала завершите", "Перерозыгрыш доступен после окончания розыгрыша."),
                ephemeral=True,
            )
            return
        await self._finish(giveaway, reroll=True, claim=False)
        embed = embeds.success("Перерозыгрыш", "Новые победители объявлены в канале.")
        embed.add_field(name="Подсказка", value=f"Окончание было: {relative(datetime.fromisoformat(giveaway['ends_at']))}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reroll", description="Переразыграть приз среди участников")
    @app_commands.describe(message_id="ID сообщения с розыгрышем")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def reroll_cmd(self, interaction: discord.Interaction, message_id: int) -> None:
        await self._do_reroll(interaction, message_id)
