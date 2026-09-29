"""Команда status — краткий статус без дублирования /ping и /health."""

from __future__ import annotations

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import MegaCog
from app.utils.time import format_duration


class HealthCog(MegaCog, name="Health"):
    """Краткий статус бота (/ping и /health живут в PingCog)."""

    @app_commands.command(name="status", description="Краткий статус бота")
    async def status(self, interaction: discord.Interaction) -> None:
        """Показывает краткий статус бота."""
        latency = round(self.bot.latency * 1000)
        quality = "отлично" if latency < 100 else "стабильно" if latency < 200 else "высокая задержка"

        embed = embeds.brand("Система на связи", f"Ответ получен за **{latency} мс** • {quality}")
        embed.add_field(name="ЗАДЕРЖКА", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="АПТАЙМ", value=f"`{format_duration(int(self.bot.uptime.total_seconds()))}`", inline=True)
        embed.add_field(name="ВЕРСИЯ", value=f"`v{self.config.version}`", inline=True)
        await interaction.response.send_message(embed=embed)
