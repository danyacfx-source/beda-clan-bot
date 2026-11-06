"""Система отпусков: панель заявок, статус, продление и отмена, информационное сообщение.

Кнопки и модалки — здесь, данные и правила — в ``VacationService``. Панель и
кнопки устойчивы к перезапуску: custom_id фиксированы и регистрируются в
``app/core/loader.py``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import tasks

from app.core import embeds
from app.core.base import ClanCog
from app.services.vacation_service import (
    VacationError,
    VacationService,
    format_period,
    info_embed,
    moscow_today,
    panel_embed,
)

if TYPE_CHECKING:
    from app.core.bot import ClanBot
    from app.types import VacationRow, VacationSettingsRow

logger = logging.getLogger("bot.cogs")

VACATION_REQUEST_ID = "vacation:request"
VACATION_STATUS_ID = "vacation:status"
VACATION_EXTEND_ID = "vacation:extend"
VACATION_CANCEL_ID = "vacation:cancel"

#: Как часто чистить прошедшие отпуска и перерисовывать информацию (минуты).
MAINTENANCE_MINUTES = 15


def _status_embed(rows: list[VacationRow], today: date) -> discord.Embed:
    if not rows:
        return embeds.info("Статус отпусков", "У вас нет активных отпусков.")
    lines = []
    for row in rows:
        state = "🟢 идёт сейчас" if date.fromisoformat(row["start_date"]) <= today else "🕓 предстоит"
        lines.append(f"**{state}**\n{format_period(row['start_date'], row['end_date'])}\nПричина: {row['reason']}")
    return embeds.info("Ваши отпуска", "\n\n".join(lines)[:4096])


class _VacationRequestModal(discord.ui.Modal, title="Заявка на отпуск"):
    start_date = discord.ui.TextInput(label="Дата начала (ДД.ММ.ГГГГ)", placeholder="05.08.2026", max_length=10)
    end_date = discord.ui.TextInput(label="Дата окончания (ДД.ММ.ГГГГ)", placeholder="23.08.2026", max_length=10)
    reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, vacations: VacationService) -> None:
        super().__init__()
        self.vacations = vacations

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id is None:
            await interaction.followup.send(embed=embeds.error("Ошибка", "Заявка работает только на сервере."), ephemeral=True)
            return
        try:
            row = await self.vacations.create(
                interaction.guild_id,
                interaction.user.id,
                str(self.start_date),
                str(self.end_date),
                str(self.reason),
            )
        except VacationError as exc:
            await interaction.followup.send(embed=embeds.error("Заявка не принята", str(exc)), ephemeral=True)
            return
        await self.vacations.refresh_info(interaction.guild_id)
        await interaction.followup.send(
            embed=embeds.success(
                "Отпуск оформлен",
                f"{format_period(row['start_date'], row['end_date'])}\nПричина: {row['reason']}",
            ),
            ephemeral=True,
        )


class _VacationExtendModal(discord.ui.Modal, title="Продление отпуска"):
    end_date = discord.ui.TextInput(label="Новая дата окончания (ДД.ММ.ГГГГ)", placeholder="30.08.2026", max_length=10)

    def __init__(self, vacations: VacationService) -> None:
        super().__init__()
        self.vacations = vacations

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id is None:
            await interaction.followup.send(embed=embeds.error("Ошибка", "Продление работает только на сервере."), ephemeral=True)
            return
        try:
            row = await self.vacations.extend(interaction.guild_id, interaction.user.id, str(self.end_date))
        except VacationError as exc:
            await interaction.followup.send(embed=embeds.error("Продление не выполнено", str(exc)), ephemeral=True)
            return
        await self.vacations.refresh_info(interaction.guild_id)
        await interaction.followup.send(
            embed=embeds.success("Отпуск продлён", format_period(row["start_date"], row["end_date"])),
            ephemeral=True,
        )


class VacationPanelView(discord.ui.View):
    """Панель системы отпусков. Устойчивая: timeout=None и фиксированные custom_id."""

    def __init__(self, vacations: VacationService) -> None:
        super().__init__(timeout=None)
        self.vacations = vacations

    @discord.ui.button(label="Взять отпуск", emoji="📝", style=discord.ButtonStyle.success, custom_id=VACATION_REQUEST_ID)
    async def request(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        # Модалка должна быть первым ответом: без defer до send_modal.
        if interaction.guild_id is None:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Работает только на сервере."), ephemeral=True)
            return
        await interaction.response.send_modal(_VacationRequestModal(self.vacations))

    @discord.ui.button(label="Статус", emoji="📋", style=discord.ButtonStyle.primary, custom_id=VACATION_STATUS_ID)
    async def status(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id is None:
            await interaction.followup.send(embed=embeds.error("Ошибка", "Работает только на сервере."), ephemeral=True)
            return
        today = moscow_today()
        rows = await self.vacations.list_for_user(interaction.guild_id, interaction.user.id, today=today)
        await interaction.followup.send(embed=_status_embed(rows, today), ephemeral=True)

    @discord.ui.button(label="Продлить отпуск", emoji="🔄", style=discord.ButtonStyle.success, custom_id=VACATION_EXTEND_ID)
    async def extend(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Работает только на сервере."), ephemeral=True)
            return
        # Проверка до модалки: если продлевать нечего, сообщаем сразу, без формы.
        target = await self.vacations.target_for(interaction.guild_id, interaction.user.id)
        if target is None:
            await interaction.response.send_message(
                embed=embeds.error("Продление недоступно", "Нет активного или предстоящего отпуска."),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(_VacationExtendModal(self.vacations))

    @discord.ui.button(label="Снять отпуск", emoji="❌", style=discord.ButtonStyle.danger, custom_id=VACATION_CANCEL_ID)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild_id is None:
            await interaction.followup.send(embed=embeds.error("Ошибка", "Работает только на сервере."), ephemeral=True)
            return
        try:
            row = await self.vacations.cancel(interaction.guild_id, interaction.user.id)
        except VacationError as exc:
            await interaction.followup.send(embed=embeds.error("Отпуск не снят", str(exc)), ephemeral=True)
            return
        await self.vacations.refresh_info(interaction.guild_id)
        await interaction.followup.send(
            embed=embeds.success("Отпуск снят", f"Отменён: {format_period(row['start_date'], row['end_date'])}"),
            ephemeral=True,
        )


class VacationsCog(ClanCog, name="Vacations"):
    def __init__(self, bot: ClanBot, vacations: VacationService) -> None:
        super().__init__(bot)
        self.vacations = vacations

    async def cog_load(self) -> None:
        if not self.maintenance_loop.is_running():
            self.maintenance_loop.start()

    async def cog_unload(self) -> None:
        self.maintenance_loop.cancel()

    @tasks.loop(minutes=MAINTENANCE_MINUTES)
    async def maintenance_loop(self) -> None:
        if self.bot.is_closed():
            return
        try:
            ended = await self.vacations.mark_ended()
            if ended:
                logger.info("Vacations: завершено прошедших отпусков: %d", ended)
            await self.vacations.refresh_all(lambda: VacationPanelView(self.vacations))
        except Exception:
            logger.exception("Vacations: ошибка фонового обслуживания")

    @maintenance_loop.before_loop
    async def before_maintenance(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="setup_vacations", description="Опубликовать информацию и панель системы отпусков")
    @app_commands.describe(channel="Канал для сообщений (по умолчанию — текущий)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup_vacations(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        guild = interaction.guild
        target = channel or interaction.channel
        if guild is None or not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Выберите текстовый канал."), ephemeral=True)
            return
        perms = target.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                embed=embeds.error(
                    "Не хватает прав бота",
                    f"В {target.mention} боту нужны «Просматривать канал», «Отправлять сообщения» и «Встраивать ссылки».",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        previous = await self.vacations.settings(guild.id)
        if previous is not None:
            await self._delete_previous(previous)

        description = await self.vacations.info_description(guild.id)
        try:
            info_message = await target.send(embed=info_embed(description), allowed_mentions=discord.AllowedMentions.none())
            try:
                panel_message = await target.send(embed=panel_embed(), view=VacationPanelView(self.vacations))
            except discord.HTTPException:
                await info_message.delete()
                raise
        except discord.HTTPException:
            logger.exception("Vacations: не удалось опубликовать сообщения в канале %s", target.id)
            await interaction.followup.send(
                embed=embeds.error("Не удалось опубликовать", "Discord отказал в отправке сообщений. Подробности в логе."),
                ephemeral=True,
            )
            return
        await self.vacations.save_setup(guild.id, target.id, info_message.id, panel_message.id)
        await interaction.followup.send(
            embed=embeds.success(
                "Система отпусков настроена",
                f"Канал: {target.mention}\nИнформация и панель опубликованы. Прежние сообщения (если были) удалены.",
            ),
            ephemeral=True,
        )

    async def _delete_previous(self, settings: VacationSettingsRow) -> None:
        """Удаляет сообщения прошлой настройки, чтобы не плодить дубли панели."""
        channel = self.bot.get_channel(int(settings["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        for key in ("info_message_id", "panel_message_id"):
            message_id = settings[key]
            if message_id is None:
                continue
            try:
                await channel.get_partial_message(int(message_id)).delete()
            except discord.HTTPException:
                logger.debug("Vacations: прежнее сообщение %s не удалено", message_id, exc_info=True)
