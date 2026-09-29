"""Команды тикетов: отправка панели и управление."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.core.ticket_content import (
    CLAN_PANEL_BUTTON,
    CLAN_PANEL_EMOJI,
    CLAN_PANEL_FOOTER,
    CLAN_PANEL_TEXT,
    CLAN_PANEL_TITLE,
)
from app.core.views import TicketOpenView
from app.services.ticket_service import TicketService

if TYPE_CHECKING:
    from app.core.bot import ClanBot

class TicketCog(ClanCog, name="Tickets"):
    # Группа обязана быть атрибутом класса кога: объявленная на уровне модуля
    # она не попадает в дерево команд, и все подкоманды молча теряются.
    ticket = app_commands.Group(name="ticket", description="Управление тикетами")

    def __init__(self, bot: ClanBot, tickets: TicketService) -> None:
        super().__init__(bot)
        self.tickets = tickets

    async def cog_unload(self) -> None:
        await self.tickets.aclose()

    @ticket.command(name="panel", description="Отправить панель открытия тикета")
    @app_commands.describe(channel="Куда отправить панель (по умолчанию — текущий канал)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Не удалось выбрать канал."), ephemeral=True)
            return
        settings = await self.services.settings.get(interaction.guild.id)
        embed = embeds.brand(
            settings.get("ticket_panel_title") or CLAN_PANEL_TITLE,
            settings.get("ticket_panel_description") or CLAN_PANEL_TEXT,
        )
        embed.set_footer(text=settings.get("ticket_panel_footer") or CLAN_PANEL_FOOTER)
        await target.send(
            embed=embed,
            view=TicketOpenView(
                self.tickets,
                label=settings.get("ticket_open_label") or CLAN_PANEL_BUTTON,
                emoji=settings.get("ticket_open_emoji") or CLAN_PANEL_EMOJI,
            ),
        )
        await interaction.response.send_message(embed=embeds.success("Панель отправлена", f"Панель в {target.mention}"), ephemeral=True)

    @ticket.command(name="info", description="Информация о текущем тикете")
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Команда работает только в текстовых каналах."),
                ephemeral=True,
            )
            return
        ticket = await self.tickets.get_open_ticket(interaction.guild.id, channel.id)
        if ticket is None:
            await interaction.response.send_message(
                embed=embeds.error("Это не тикет", "В этом канале нет открытого тикета."),
                ephemeral=True,
            )
            return
        embed = embeds.info("Тикет", f"Номер: `{ticket['ticket_id']}`\nАвтор: <@{ticket['creator_id']}>")
        await interaction.response.send_message(embed=embed)
