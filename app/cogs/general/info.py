"""Команды информации: сервер и пользователь."""

from __future__ import annotations

import datetime as dt

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import MegaCog

_BOT_BADGES = {
    "partner": "🤝 Partner",
    "verified_bot_developer": "👨‍💻 Verified Dev",
    "discord_certified_moderator": "🛡️ Certified Moderator",
}


class InfoCog(MegaCog, name="Информация"):
    @app_commands.command(name="serverinfo", description="Информация о сервере")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                embed=embeds.error("Недоступно", "Команда работает только на сервере."),
                ephemeral=True,
            )
            return

        members = guild.members
        total_members = sum(1 for m in members if not m.bot)
        total_bots = sum(1 for m in members if m.bot)

        embed = embeds.brand(guild.name, guild.description or "Панель состояния сервера")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="ВЛАДЕЛЕЦ", value=guild.owner.mention if guild.owner else "—", inline=True)
        embed.add_field(name="СОЗДАН", value=discord.utils.format_dt(guild.created_at, "D"), inline=True)
        embed.add_field(name="УРОВЕНЬ", value=f"`{guild.premium_tier}`", inline=True)
        embed.add_field(
            name="УЧАСТНИКИ",
            value=f"**{total_members}** человек\n`{total_bots} ботов`",
            inline=True,
        )
        embed.add_field(name="КАНАЛЫ", value=f"**{len(guild.channels)}**", inline=True)
        embed.add_field(name="РОЛИ / БУСТЫ", value=f"**{len(guild.roles)}** / **{guild.premium_subscription_count}**", inline=True)
        embed.set_footer(text=f"Асуна Юки  •  SERVER ID {guild.id}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Информация о пользователе")
    @app_commands.describe(user="Пользователь (по умолчанию — вы)")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        target = user or interaction.user
        embed = embeds.brand(target.display_name, target.mention)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="АККАУНТ СОЗДАН", value=discord.utils.format_dt(target.created_at, "D"), inline=True)
        embed.add_field(name="НА СЕРВЕРЕ С", value=discord.utils.format_dt(target.joined_at, "D") if target.joined_at else "—", inline=True)
        top_role = target.top_role if target.top_role.color.value else None
        embed.add_field(name="ГЛАВНАЯ РОЛЬ", value=top_role.mention if top_role else "—", inline=True)
        if target.premium_since:
            embed.add_field(name="БУСТЕР", value="Да", inline=True)
        roles = [r.mention for r in target.roles[1:][:10]]
        if roles:
            embed.add_field(name=f"РОЛИ · {len(target.roles) - 1}", value=" ".join(roles), inline=False)
        if target.id == interaction.guild.owner_id:
            embed.add_field(name="ВЛАДЕЛЕЦ СЕРВЕРА", value="Да", inline=True)
        embed.set_footer(text=f"Асуна Юки  •  USER ID {target.id}")
        await interaction.response.send_message(embed=embed)


class AboutCog(MegaCog, name="About"):
    @app_commands.command(name="about", description="О боте")
    async def about(self, interaction: discord.Interaction) -> None:
        guilds = len(self.bot.guilds)
        uptime = dt.datetime.now(tz=dt.UTC) - self.bot.start_time

        embed = embeds.brand("Асуна Юки", "Система управления Discord-сообществом.")
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="СЕРВЕРОВ", value=f"`{guilds}`", inline=True)
        embed.add_field(name="ВЕРСИЯ", value=f"`v{self.bot.config.version}`", inline=True)
        embed.add_field(name="АПТАЙМ", value=f"`{str(uptime).split('.')[0]}`", inline=True)
        public_flags = self.bot.user.public_flags
        if public_flags:
            badges = [_BOT_BADGES[name] for name, label in _BOT_BADGES.items() if getattr(public_flags, name, False)]
            if badges:
                embed.add_field(name="Бейджи", value=", ".join(badges), inline=False)
        await interaction.response.send_message(embed=embed)
