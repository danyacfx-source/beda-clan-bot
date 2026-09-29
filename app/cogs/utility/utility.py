"""Утилиты сервера: lock/unlock, инфо-команды, аватары."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.core.checks import bot_has_permissions
from app.utils.format import relative
from app.utils.pagination import PaginatorView

if TYPE_CHECKING:
    pass


class UtilityCog(ClanCog, name="Utility"):
    async def _channel_or_current(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None
    ) -> discord.TextChannel | None:
        if channel is not None:
            return channel
        return interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None

    @app_commands.command(name="lock", description="Запретить @everyone писать в канал")
    @app_commands.describe(channel="Канал (по умолчанию текущий)")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    @bot_has_permissions(manage_channels=True, manage_roles=True)
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = await self._channel_or_current(interaction, channel)
        if target is None:
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Команда работает только в текстовом канале."),
                ephemeral=True,
            )
            return
        everyone = interaction.guild.default_role
        await target.set_permissions(everyone, send_messages=False, reason=f"Lock by {interaction.user}")
        await interaction.response.send_message(embed=embeds.success("Канал закрыт", target.mention))

    @app_commands.command(name="unlock", description="Разрешить @everyone писать в канал")
    @app_commands.describe(channel="Канал (по умолчанию текущий)")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    @bot_has_permissions(manage_channels=True, manage_roles=True)
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = await self._channel_or_current(interaction, channel)
        if target is None:
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Команда работает только в текстовом канале."),
                ephemeral=True,
            )
            return
        everyone = interaction.guild.default_role
        await target.set_permissions(everyone, send_messages=None, reason=f"Unlock by {interaction.user}")
        await interaction.response.send_message(embed=embeds.success("Канал открыт", target.mention))

    @app_commands.command(name="avatar", description="Показать аватар пользователя")
    @app_commands.describe(user="Пользователь (по умолчанию вы)")
    async def avatar(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        user = user or interaction.user
        avatar = user.display_avatar
        embed = embeds.info(f"Аватар: {user}")
        embed.set_image(url=avatar.with_size(1024).url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="servericon", description="Показать иконку сервера")
    @app_commands.guild_only()
    async def server_icon(self, interaction: discord.Interaction) -> None:
        if interaction.guild.icon is None:
            await interaction.response.send_message(embed=embeds.info("У сервера нет иконки"), ephemeral=True)
            return
        embed = embeds.info(f"Иконка: {interaction.guild.name}")
        embed.set_image(url=interaction.guild.icon.with_size(1024).url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="emoji", description="Список эмодзи сервера")
    @app_commands.guild_only()
    async def emoji_list(self, interaction: discord.Interaction) -> None:
        emojis = sorted(interaction.guild.emojis, key=lambda e: e.name.lower())
        if not emojis:
            await interaction.response.send_message(embed=embeds.info("Пусто", "На сервере нет эмодзи."), ephemeral=True)
            return
        pages: list[discord.Embed] = []
        per_page = 20
        for start in range(0, len(emojis), per_page):
            embed = embeds.info(f"Эмодзи сервера ({len(emojis)})")
            embed.description = "\n".join(f"{emoji} `{emoji.name}`" for emoji in emojis[start : start + per_page])
            pages.append(embed)
        if len(pages) == 1:
            await interaction.response.send_message(embed=pages[0])
        else:
            await interaction.response.send_message(embed=pages[0], view=PaginatorView(pages, interaction.user))

    @app_commands.command(name="roleinfo", description="Информация о роли")
    @app_commands.describe(role="Роль")
    @app_commands.guild_only()
    async def role_info(self, interaction: discord.Interaction, role: discord.Role) -> None:
        permissions = [
            name
            for name in (
                "administrator",
                "manage_guild",
                "manage_roles",
                "manage_channels",
                "manage_messages",
                "moderate_members",
                "ban_members",
                "kick_members",
            )
            if getattr(role.permissions, name, False)
        ]
        embed = embeds.info(f"Роль: {role.name}", role.mention)
        embed.add_field(name="ID", value=role.id, inline=True)
        embed.add_field(name="Участников", value=len(role.members), inline=True)
        embed.add_field(name="Цвет", value=str(role.color), inline=True)
        embed.add_field(name="Позиция", value=role.position, inline=True)
        embed.add_field(name="Отображается отдельно", value="Да" if role.hoist else "Нет", inline=True)
        embed.add_field(name="Создана", value=relative(role.created_at), inline=True)
        embed.add_field(name="Ключевые права", value=", ".join(permissions) or "—", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="channelinfo", description="Информация о канале")
    @app_commands.describe(channel="Канал (по умолчанию текущий)")
    @app_commands.guild_only()
    async def channel_info(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = await self._channel_or_current(interaction, channel)
        if target is None:
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Команда работает только в текстовом канале."),
                ephemeral=True,
            )
            return
        embed = embeds.info(f"Канал: #{target.name}", target.mention)
        embed.add_field(name="ID", value=target.id, inline=True)
        embed.add_field(name="Категория", value=target.category.name if target.category else "—", inline=True)
        embed.add_field(name="Позиция", value=target.position, inline=True)
        embed.add_field(name="NSFW", value="Да" if target.is_nsfw() else "Нет", inline=True)
        embed.add_field(name="Слоумод", value=f"{target.slowmode_delay} сек" if target.slowmode_delay else "выкл", inline=True)
        if target.topic:
            embed.add_field(name="Тема", value=target.topic[:1024], inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="whois", description="Информация об участнике")
    @app_commands.describe(user="Участник (по умолчанию вы)")
    @app_commands.guild_only()
    async def whois(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        member = user or interaction.user
        roles = "\n".join(role.mention for role in reversed(member.roles[1:15])) or "—"
        embed = embeds.info(f"Участник: {member}", member.mention)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Бот", value="Да" if member.bot else "Нет", inline=True)
        embed.add_field(name="Топ-роль", value=member.top_role.mention, inline=True)
        embed.add_field(name="Аккаунт создан", value=relative(member.created_at), inline=True)
        embed.add_field(name="Присоединился", value=relative(member.joined_at), inline=True)
        embed.add_field(name="Статус", value=str(member.status), inline=True)
        embed.add_field(name=f"Роли ({len(member.roles) - 1})", value=roles, inline=False)
        await interaction.response.send_message(embed=embed)
