"""Пагинация embed-сообщений через кнопки."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord import Interaction, User


class PaginatorView(discord.ui.View):
    """Листаемые embed-страницы. Привязан к пользователю."""

    def __init__(self, embeds: Sequence[discord.Embed], user: User, *, timeout: float = 180.0) -> None:
        super().__init__(timeout=timeout)
        self.embeds = list(embeds)
        self.user = user
        self.index = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.index == 0
        self.next_button.disabled = self.index >= len(self.embeds) - 1

    @discord.ui.button(label="\u25c0", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: Interaction, _button: discord.ui.Button) -> None:
        if interaction.user != self.user:
            await interaction.response.defer()
            return
        self.index = max(0, self.index - 1)
        await self._show(interaction)

    @discord.ui.button(label="\u25b6", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: Interaction, _button: discord.ui.Button) -> None:
        if interaction.user != self.user:
            await interaction.response.defer()
            return
        self.index = min(len(self.embeds) - 1, self.index + 1)
        await self._show(interaction)

    async def _show(self, interaction: Interaction) -> None:
        self._update_buttons()
        embed = self.embeds[self.index].copy()
        embed.set_footer(text=f"Страница {self.index + 1} / {len(self.embeds)}")
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        self.prev_button.disabled = True
        self.next_button.disabled = True
