"""Карточка «Где играем»: сервер сбора, команда и комнаты коллеров.

Перенос ``app/where_play.py`` из beda-discord-bot: логика в сервисе, права и
проверки — в коге, состояние — в БД (переживает рестарт).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.core import embeds
from app.core.base import ClanCog
from app.services.where_play_service import TEAMS, WherePlayError

if TYPE_CHECKING:
    from app.core.bot import ClanBot
    from app.services.where_play_service import WherePlayService
    from app.types import WherePlayRow

logger = logging.getLogger("bot.cogs")


class WherePlayCog(ClanCog, name="WherePlay"):
    def __init__(self, bot: ClanBot, where_play: WherePlayService) -> None:
        super().__init__(bot)
        self.where_play = where_play

    async def cog_load(self) -> None:
        self.poll_loop.change_interval(seconds=self.config.where_play_poll_seconds)
        self.poll_loop.start()

    async def cog_unload(self) -> None:
        self.poll_loop.cancel()
        await self.where_play.close()

    @tasks.loop(seconds=60.0)
    async def poll_loop(self) -> None:
        if self.bot.is_closed():
            return
        try:
            failures = await self.where_play.refresh()
        except Exception:
            logger.exception("Ошибка обновления карточек «Где играем»")
            return
        if failures:
            logger.warning("Карточек «Где играем» не обновлено: %d", failures)

    @poll_loop.before_loop
    async def before_poll(self) -> None:
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            self.poll_loop.cancel()

    # --- команды ---

    @app_commands.command(name="setup_where_play", description="Настроить канал карточки «Где играем» и роль коллеров")
    @app_commands.describe(channel="Канал для карточки", caller_role="Роль коллеров (необязательно)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_where_play(
        self, interaction: discord.Interaction, channel: discord.TextChannel, caller_role: discord.Role | None = None
    ) -> None:
        if caller_role is not None and (caller_role.is_default() or caller_role.managed):
            await interaction.response.send_message(embed=embeds.error("Не та роль", "Выберите обычную роль коллеров."), ephemeral=True)
            return
        me = interaction.guild.me if interaction.guild else None
        if me is None or not all(getattr(me.guild_permissions, name, False) for name in ("view_channel", "send_messages", "embed_links")):
            await interaction.response.send_message(
                embed=embeds.error(
                    "Не хватает прав бота",
                    "Боту нужны права «Просматривать канал», «Отправлять сообщения» и «Встраивать ссылки».",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        previous = await self.where_play.row(interaction.guild_id)
        if previous is not None and previous["message_id"] and previous["channel_id"] != channel.id:
            old = self.bot.get_channel(int(previous["channel_id"]))
            if isinstance(old, discord.TextChannel):
                try:
                    await old.get_partial_message(int(previous["message_id"])).delete()
                except discord.HTTPException:
                    logger.debug("Не удалось удалить прежнюю карточку «Где играем»", exc_info=True)
        row = await self.where_play.configure_channel(interaction.guild_id, channel.id, caller_role.id if caller_role else None)
        if row is not None:
            await self.where_play.publish(row, "ℹ️ Ожидается очередное обновление данных." if row["active"] else None)
        await interaction.followup.send(
            embed=embeds.success(
                "Карточка настроена",
                f"Канал: {channel.mention}. Дальше — /where_play и /stop_play.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="where_play", description="Указать сервер и команду общего сбора")
    @app_commands.describe(
        server="Код подключения из игры: число или UUID community-сервера, НЕ название сервера",
        team="Наша команда",
    )
    @app_commands.choices(team=[app_commands.Choice(name=name, value=name) for name in TEAMS])
    @app_commands.guild_only()
    async def where_play(self, interaction: discord.Interaction, server: str, team: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        row = await self.where_play.row(interaction.guild_id)
        if row is None:
            await interaction.followup.send(embed=embeds.error("Не настроено", "Сначала выполните /setup_where_play."), ephemeral=True)
            return
        if not self.where_play.is_manager(row, interaction.user):
            await interaction.followup.send(
                embed=embeds.error("Недостаточно прав", "Управление доступно администраторам и роли коллеров."),
                ephemeral=True,
            )
            return
        try:
            updated, warning = await self.where_play.select_server(interaction.guild_id, server, team, interaction.user.id)
        except WherePlayError as exc:
            await interaction.followup.send(embed=embeds.error("Не получилось", str(exc)), ephemeral=True)
            return
        if updated is not None:
            await self.where_play.publish(updated, warning)
        await interaction.followup.send(
            embed=embeds.success("Сервер обновлён", f"Карточка в <#{row['channel_id']}> обновлена." + (f"\n{warning}" if warning else "")),
            ephemeral=True,
        )

    @app_commands.command(name="stop_play", description="Завершить общий сбор")
    @app_commands.guild_only()
    async def stop_play(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        row = await self.where_play.row(interaction.guild_id)
        if row is None:
            await interaction.followup.send(embed=embeds.error("Не настроено", "Сначала выполните /setup_where_play."), ephemeral=True)
            return
        if not self.where_play.is_manager(row, interaction.user):
            await interaction.followup.send(
                embed=embeds.error("Недостаточно прав", "Нужны права администратора или роль коллера."), ephemeral=True
            )
            return
        if not row["active"]:
            await interaction.followup.send(embed=embeds.info("Уже завершён", "Общего сбора сейчас нет."), ephemeral=True)
            return
        stopped = await self.where_play.stop(interaction.guild_id)
        if stopped is not None:
            await self.where_play.publish(stopped)
        await interaction.followup.send(embed=embeds.success("Сбор завершён", "Карточка обновлена."), ephemeral=True)

    @app_commands.command(name="where_play_status", description="Состояние карточки «Где играем»")
    @app_commands.guild_only()
    async def where_play_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        row = await self._row_or_reply(interaction)
        if row is None:
            return
        embed = self.where_play.card(row, None, await self.where_play.caller_lines(row))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="where_play_card", description="Отправить карточку «Где играем» в текущий канал")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def where_play_card(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Карточка отправляется только в текстовый канал."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        row = await self._row_or_reply(interaction)
        if row is None:
            return
        await self.where_play.configure_channel(interaction.guild_id, interaction.channel_id, row["role_id"])
        fresh = await self.where_play.row(interaction.guild_id)
        if fresh is not None:
            await self.where_play.publish(fresh)
        await interaction.followup.send(
            embed=embeds.success("Карточка отправлена", f"Канал: {interaction.channel.mention}"), ephemeral=True
        )

    async def _row_or_reply(self, interaction: discord.Interaction) -> WherePlayRow | None:
        row = await self.where_play.row(interaction.guild_id)
        if row is None:
            await interaction.followup.send(embed=embeds.error("Не настроено", "Сначала выполните /setup_where_play."), ephemeral=True)
            return None
        return row

    # --- чистка комнат коллеров ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.bot:
            return
        channel = before.channel
        if not isinstance(channel, discord.VoiceChannel) or channel.members:
            return
        row = await self.where_play.row(member.guild.id)
        if row is None or not row["active"]:
            return
        await self.where_play.forget_caller_room(member.guild.id, member.id)
        try:
            await self.where_play.publish(row)
        except Exception:
            logger.debug("Не удалось обновить карточку «Где играем» после выхода коллера", exc_info=True)
