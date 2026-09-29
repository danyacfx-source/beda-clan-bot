"""Опросы: создание, голосование кнопками, подведение итогов."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.core.views import PollView
from app.services.poll_service import PollService

if TYPE_CHECKING:
    from app.core.bot import ClanBot

_MAX_OPTIONS = 5


class PollsCog(ClanCog, name="Polls"):
    def __init__(self, bot: ClanBot, polls: PollService) -> None:
        super().__init__(bot)
        self.polls = polls

    @app_commands.command(name="poll", description="Создать опрос (2–5 вариантов)")
    @app_commands.describe(
        question="Вопрос опроса",
        option1="Вариант 1 (обязателен)",
        option2="Вариант 2",
        option3="Вариант 3",
        option4="Вариант 4",
        option5="Вариант 5",
    )
    @app_commands.guild_only()
    async def poll_create(
        self,
        interaction: discord.Interaction,
        question: str,
        option1: str,
        option2: str | None = None,
        option3: str | None = None,
        option4: str | None = None,
        option5: str | None = None,
    ) -> None:
        question = question.strip()
        options: list[str] = []
        seen: set[str] = set()
        for raw in (option1, option2, option3, option4, option5):
            if not raw or raw.strip().lower() == "нет":
                continue
            option = raw.strip()[:100]
            key = option.casefold()
            if option and key not in seen:
                options.append(option)
                seen.add(key)
        if not question:
            await interaction.response.send_message(
                embed=embeds.error("Пустой вопрос", "Напишите вопрос для опроса."),
                ephemeral=True,
            )
            return
        if len(options) < 2:
            await interaction.response.send_message(embed=embeds.error("Мало вариантов", "Минимум 2 варианта ответа."), ephemeral=True)
            return
        if len(options) > _MAX_OPTIONS:
            options = options[:_MAX_OPTIONS]
        question = question[:256]

        poll_id = await self.polls.create(interaction.guild.id, interaction.channel.id, interaction.user.id, question, options)
        embed = await self.polls.embed(poll_id)
        view = PollView(poll_id, len(options))
        try:
            await interaction.response.send_message(embed=embed, view=view)
            message = await interaction.original_response()
            await self.polls.bind_message(poll_id, message.id)
            self.bot.add_view(view, message_id=message.id)
        except Exception:
            await self.polls.end(poll_id)
            raise

    @app_commands.command(name="poll_end", description="Завершить опрос и показать итоги")
    @app_commands.describe(poll_id="ID опроса (в футере сообщения)")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def poll_end(self, interaction: discord.Interaction, poll_id: int) -> None:
        poll = await self.polls.get(poll_id)
        if poll is None:
            await interaction.response.send_message(embed=embeds.error("Не найдено", "Опроса с таким ID нет."), ephemeral=True)
            return
        if poll["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(
                embed=embeds.error("Недоступно", "Этот опрос принадлежит другому серверу."),
                ephemeral=True,
            )
            return
        if not poll["active"]:
            await interaction.response.send_message(embed=embeds.warning("Уже завершён", "Этот опрос уже закрыт."), ephemeral=True)
            return

        question, options, counts = await self.polls.end(poll_id)

        result = embeds.info(f"📊 Итоги: {question}")
        total = sum(counts.values())
        for index, option in enumerate(options):
            votes = counts.get(index, 0)
            percent = round(votes / total * 100) if total else 0
            result.add_field(name=option, value=f"**{votes}** голосов ({percent}%)", inline=False)
        result.set_footer(text=f"ID опроса: {poll_id} • Всего голосов: {total}")

        if poll["message_id"]:
            channel = self.bot.get_channel(poll["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(poll["message_id"])
                    await message.edit(embed=result, view=None)
                except discord.HTTPException:
                    pass
        await interaction.response.send_message(embed=embeds.success("Опрос завершён", "Итоги выведены в канал."), ephemeral=True)
