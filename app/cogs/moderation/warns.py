"""Варн-панель: точечное управление предупреждениями."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.core.views import ConfirmView
from app.services.moderation_service import ModerationService
from app.utils.format import plural

if TYPE_CHECKING:
    from app.core.bot import ClanBot


class WarnsCog(ClanCog, name="Warns"):
    def __init__(self, bot: ClanBot, moderation: ModerationService) -> None:
        super().__init__(bot)
        self.moderation = moderation

    @app_commands.command(name="warn_remove", description="Удалить одно предупреждение")
    @app_commands.describe(warn_id="ID из списка /warns")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn_remove(self, interaction: discord.Interaction, warn_id: int) -> None:
        if await self.moderation.remove_warn(interaction.guild.id, warn_id):
            embed = embeds.success("Предупреждение снято", f"ID `{warn_id}` удалён.")
        else:
            embed = embeds.error("Не найдено", "Предупреждение не найдено.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="warn_clear", description="Удалить все предупреждения участника")
    @app_commands.describe(user="Участник")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn_clear(self, interaction: discord.Interaction, user: discord.Member) -> None:
        count = await self.moderation.warn_count(interaction.guild.id, user.id)
        if count == 0:
            await interaction.response.send_message(embed=embeds.info("Нечего чистить"), ephemeral=True)
            return

        async def on_confirm(_: discord.Interaction) -> None:
            cleared = await self.moderation.clear_warns(interaction.guild.id, user.id)
            words = plural(cleared, "предупреждение", "предупреждения", "предупреждений")
            result = embeds.success("Готово", f"У {user.mention} удалено **{cleared}** {words}.")
            await interaction.edit_original_response(embed=result, view=None)

        async def on_cancel(_: discord.Interaction) -> None:
            canceled = embeds.info("Отменено")
            await interaction.edit_original_response(embed=canceled, view=None)

        view = ConfirmView(on_confirm=on_confirm, on_cancel=on_cancel, user=interaction.user, confirm_emoji="✅", cancel_emoji="❌")
        question = f"Удалить все **{count}** предупреждений у {user.mention}?"
        await interaction.response.send_message(embed=embeds.warning("Подтверждение", question), view=view)
