"""Команда ping."""

from __future__ import annotations

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.utils.time import format_duration


class PingCog(ClanCog, name="General"):
    @app_commands.command(name="ping", description="Проверка задержки бота")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency = round(self.bot.latency * 1000)
        quality = "отлично" if latency < 100 else "стабильно" if latency < 200 else "высокая задержка"
        embed = embeds.brand("Система на связи", f"Ответ получен за **{latency} мс** · {quality}")
        embed.add_field(name="ЗАДЕРЖКА", value=f"`{latency} ms`", inline=True)
        embed.add_field(name="АПТАЙМ", value=f"`{format_duration(int(self.bot.uptime.total_seconds()))}`", inline=True)
        embed.add_field(name="ВЕРСИЯ", value=f"`v{self.config.version}`", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="health", description="Подробная диагностика состояния бота")
    async def health(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        db_ready = db is not None and getattr(db, "_conn", None) is not None
        enabled = [
            label
            for label, value in (
                ("панель", self.config.panel_port is not None),
                ("напоминания", True),
            )
            if value
        ]
        embed = embeds.brand("Диагностика", "Сводка состояния основных компонентов бота.")
        embed.add_field(name="Discord", value="🟢 подключён" if self.bot.is_ready() else "🟡 подключается", inline=True)
        embed.add_field(name="База данных", value="🟢 готова" if db_ready else "🔴 недоступна", inline=True)
        embed.add_field(name="Задержка", value=f"`{round(self.bot.latency * 1000)} мс`", inline=True)
        embed.add_field(name="Коги", value=f"`{len(self.bot.cogs)}`", inline=True)
        embed.add_field(name="Голосовые подключения", value=f"`{len(self.bot.voice_clients)}`", inline=True)
        embed.add_field(name="Опциональные модули", value=", ".join(enabled) or "не включены", inline=False)
        embed.set_footer(text=f"BEDA • uptime {format_duration(int(self.bot.uptime.total_seconds()))}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
