"""Snipe: показывает последние удалённые и изменённые сообщения."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from app.core import embeds
from app.core.base import MegaCog
from app.utils.format import relative

if TYPE_CHECKING:
    from app.core.bot import MegaBot

_MAX_ENTRIES = 10


class SnipeCog(MegaCog, name="Snipe"):
    def __init__(self, bot: MegaBot) -> None:
        super().__init__(bot)
        self._deleted: dict[tuple[int, int], deque[discord.Message]] = defaultdict(lambda: deque(maxlen=_MAX_ENTRIES))
        self._edited: dict[tuple[int, int], deque[discord.Message]] = defaultdict(lambda: deque(maxlen=_MAX_ENTRIES))

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        self._deleted[(message.guild.id, message.channel.id)].append(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.author.bot or before.guild is None:
            return
        if before.content == after.content:
            return
        self._edited[(before.guild.id, before.channel.id)].append(before)

    @staticmethod
    def _mention_author(message: discord.Message) -> str:
        return f"{message.author.mention} ({message.author.display_name})"

    def _embed(self, message: discord.Message) -> discord.Embed:
        content = message.content or "*нет текста, только вложение*"
        embed = embeds.info(f"✏️ Изменённое сообщение: {message.author}", content[:1000])
        embed.add_field(name="Когда", value=relative(message.created_at), inline=True)
        if message.attachments:
            embed.add_field(name="Вложения", value="\n".join(a.url for a in message.attachments), inline=False)
        return embed

    @app_commands.command(name="snipe", description="Показать последние удалённые сообщения в канале")
    @app_commands.guild_only()
    async def snipe(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Недоступно", "Команда работает только в текстовом канале сервера."),
                ephemeral=True,
            )
            return
        messages = self._deleted.get((interaction.guild.id, interaction.channel.id))
        if not messages:
            await interaction.response.send_message(embed=embeds.info("Пусто", "Удалённых сообщений в этом канале нет."), ephemeral=True)
            return
        latest = messages[-1]
        embed = embeds.warning("🗑️ Удалённое сообщение", latest.content or "*нет текста*")
        embed.add_field(name="Автор", value=self._mention_author(latest), inline=True)
        embed.add_field(name="Когда", value=relative(latest.created_at), inline=True)
        if latest.attachments:
            embed.add_field(name="Вложения", value="\n".join(a.url for a in latest.attachments), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="editsnipe", description="Показать последние изменённые сообщения в канале")
    @app_commands.guild_only()
    async def editsnipe(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Недоступно", "Команда работает только в текстовом канале сервера."),
                ephemeral=True,
            )
            return
        messages = self._edited.get((interaction.guild.id, interaction.channel.id))
        if not messages:
            await interaction.response.send_message(embed=embeds.info("Пусто", "Изменённых сообщений в этом канале нет."), ephemeral=True)
            return
        latest = messages[-1]
        embed = self._embed(latest)
        embed.add_field(name="Автор", value=self._mention_author(latest), inline=True)
        await interaction.response.send_message(embed=embed)
