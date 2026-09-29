"""Логирование событий: удаление/изменение сообщений, участники, голосовые, баны, рестарт."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.core import embeds
from app.core.base import ClanCog
from app.services.logging_service import LoggingService

if TYPE_CHECKING:
    from app.core.bot import ClanBot


class LoggingEventsCog(ClanCog, name="AuditLog"):
    def __init__(self, bot: ClanBot, logging: LoggingService) -> None:
        super().__init__(bot)
        self.logging = logging
        self._restart_logged = False

    async def _ignored(self, channel: discord.abc.GuildChannel | None) -> bool:
        if channel is None:
            return False
        conf = await self.module_config(channel.guild.id, "logs")
        if channel.id in conf["ignore_channels"]:
            return True
        category = getattr(channel, "category", None)
        return category is not None and category.id in conf["ignore_categories"]

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._restart_logged:
            return
        self._restart_logged = True
        for guild in self.bot.guilds:
            embed = embeds.brand("Система запущена", "Все модули загружены, соединение с Discord установлено.")
            if self.bot.user is not None:
                embed.add_field(name="БОТ", value=self.bot.user.mention, inline=True)
            embed.add_field(name="СЕРВЕРОВ", value=f"`{len(self.bot.guilds)}`", inline=True)
            embed.add_field(name="ВЕРСИЯ", value=f"`v{self.bot.config.version}`", inline=True)
            await self.logging.send_embed(guild, embed, category="bot")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or await self._ignored(message.channel):
            return
        embed = embeds.warning("Сообщение удалено", message.clean_content[:900] or "(нет текста)")
        embed.add_field(name="Автор", value=f"{message.author.mention} ({message.author.id})")
        embed.add_field(name="Канал", value=f"<#{message.channel.id}>")
        await self.logging.send_embed(message.guild, embed, category="message")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.author.bot or after.guild is None or await self._ignored(after.channel):
            return
        if before.content == after.content:
            return
        embed = embeds.warning("Сообщение изменено")
        embed.add_field(name="Было", value=before.content[:900] or "—", inline=False)
        embed.add_field(name="Стало", value=after.content[:900] or "—", inline=False)
        embed.add_field(name="Автор", value=f"{after.author.mention} ({after.author.id})")
        embed.add_field(name="Канал", value=f"<#{after.channel.id}>")
        await self.logging.send_embed(after.guild, embed, category="message")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        embed = embeds.success("Участник зашёл", f"{member.mention} ({member.id})")
        embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="R"))
        await self.logging.send_embed(member.guild, embed, category="member")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        embed = embeds.warning("Участник вышел", f"{member.mention} ({member.id})")
        await self.logging.send_embed(member.guild, embed, category="member")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        changes: list[str] = []
        if before.nick != after.nick:
            changes.append(f"Ник: `{before.nick or before.name}` → `{after.nick or after.name}`")
        if before.roles != after.roles:
            added = [r.mention for r in after.roles if r not in before.roles]
            removed = [r.mention for r in before.roles if r not in after.roles]
            if added:
                changes.append(f"Выданы роли: {', '.join(added)}")
            if removed:
                changes.append(f"Сняты роли: {', '.join(removed)}")
        if not changes:
            return
        embed = embeds.info("Участник обновлён", "\n".join(changes))
        embed.add_field(name="Участник", value=f"{after.mention} ({after.id})")
        await self.logging.send_embed(after.guild, embed, category="mod")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel == after.channel:
            return
        if after.channel is not None and before.channel is None:
            embed = embeds.success("Зайшёл в голосовой", f"{member.mention} → <#{after.channel.id}>")
        elif after.channel is None and before.channel is not None:
            embed = embeds.warning("Вышел из голосового", f"{member.mention} ← <#{before.channel.id}>")
        else:
            embed = embeds.info("Перемещение в голосовом", f"{member.mention}: <#{before.channel.id}> → <#{after.channel.id}>")
        await self.logging.send_embed(member.guild, embed, category="voice")

    @commands.Cog.listener()
    async def on_guild_ban_add(self, guild: discord.Guild, user: discord.User) -> None:
        embed = embeds.warning("Пользователь забанен", f"{user.mention} ({user.id})")
        await self.logging.send_embed(guild, embed, category="mod")

    @commands.Cog.listener()
    async def on_guild_ban_remove(self, guild: discord.Guild, user: discord.User) -> None:
        embed = embeds.success("Пользователь разбанен", f"{user.mention} ({user.id})")
        await self.logging.send_embed(guild, embed, category="mod")
