"""Мастер объявлений: «старт» в личке → выбор сервера и канала → текст и картинка.

Диалог целиком идёт в личных сообщениях, а результат публикуется в канал
сервера от имени бота. Адресат сохраняется в БД, поэтому переживает рестарт
между шагами.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from app.core import embeds
from app.core.base import ClanCog

if TYPE_CHECKING:
    from app.services.announce_service import AnnounceService, AnnounceTarget

logger = logging.getLogger("bot")

#: Триггеры, с которых начинается мастер. Регистр не важен.
TRIGGERS = frozenset({"старт", "start", "/start"})
#: Ответ, означающий «пропустить этот шаг».
SKIP = "-"
#: Сколько каналов помещается на одну страницу выбора.
CHANNELS_PER_PAGE = 24


def can_publish(member: discord.Member) -> bool:
    """Право пользователя на публикацию в гильде."""
    perms = member.guild_permissions
    return perms.manage_guild or perms.administrator


class GuildSelectView(discord.ui.View):
    """Шаг 1: сервер."""

    def __init__(self, guilds: list[discord.Guild], store: AnnounceService) -> None:
        super().__init__(timeout=120)
        self.store = store
        options = [discord.SelectOption(label=g.name, value=str(g.id)) for g in guilds[:25]]
        select = discord.ui.Select(placeholder="Выберите сервер...", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        guild = interaction.client.get_guild(int(interaction.data["values"][0]))
        if guild is None:
            await interaction.response.send_message(embed=embeds.error("Сервер не найден"), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.info("Шаг 2", f"Выберите канал в **{guild.name}**:"),
            view=ChannelSelectView(guild, self.store),
        )


class ChannelSelectView(discord.ui.View):
    """Шаг 2: канал, с постраничным списком."""

    def __init__(self, guild: discord.Guild, store: AnnounceService, page: int = 0) -> None:
        super().__init__(timeout=120)
        self.guild = guild
        self.store = store
        self.channels = sorted(guild.text_channels, key=lambda ch: ch.name)
        self.page = 0
        self._pages = max(1, -(-len(self.channels) // CHANNELS_PER_PAGE)) if self.channels else 1
        self.page = min(page, self._pages - 1)
        self._rebuild()

    def _rebuild(self) -> None:
        self.clear_items()
        if self.channels:
            start = self.page * CHANNELS_PER_PAGE
            chunk = self.channels[start : start + CHANNELS_PER_PAGE]
            options = [discord.SelectOption(label=f"#{ch.name}", value=str(ch.id)) for ch in chunk]
            select = discord.ui.Select(placeholder="Выберите канал...", options=options)
        else:
            select = discord.ui.Select(
                placeholder="Нет каналов на этом сервере",
                options=[discord.SelectOption(label="Нет каналов", value="none")],
                disabled=True,
            )
        select.callback = self._on_select
        self.add_item(select)

        prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
        prev.callback = self._on_prev
        self.add_item(prev)

        counter = discord.ui.Button(
            label=f"{self.page + 1} / {self._pages}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.add_item(counter)

        nxt = discord.ui.Button(
            label="▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self._pages - 1,
        )
        nxt.callback = self._on_next
        self.add_item(nxt)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page < self._pages - 1:
            self.page += 1
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        from app.services.announce_service import AnnounceTarget

        # В личке interaction.guild всегда None, поэтому гильдию берём у себя.
        channel = self.guild.get_channel(int(interaction.data["values"][0]))
        if channel is None:
            await interaction.response.send_message(embed=embeds.error("Канал не найден"), ephemeral=True)
            return
        await self.store.set_target(
            interaction.user.id,
            AnnounceTarget(guild_id=self.guild.id, channel_id=channel.id),
        )
        await interaction.response.send_message(
            embed=embeds.info(
                "Шаг 3",
                f"Напишите **текст** объявления в этот личный чат.\n"
                f"Канал: **#{channel.name}**\n"
                f"Начните с `{SKIP}`, чтобы пропустить текст и сразу перейти к картинке.",
            )
        )


class AnnounceCog(ClanCog, name="Announce"):
    def __init__(self, bot, announce: AnnounceService) -> None:
        super().__init__(bot)
        self.store = announce
        # user_id -> {"target": AnnounceTarget, "phase": "text"|"image", "text": str|None}
        self._state: dict[int, dict] = {}

    # --- мастер в личке ---

    def _publishable_guilds(self, user_id: int) -> list[discord.Guild]:
        return [
            guild
            for guild in self.bot.guilds
            if (member := guild.get_member(user_id)) is not None and can_publish(member)
        ]

    async def _state_for(self, user_id: int) -> dict | None:
        """Текущий шаг мастера; поднимает состояние из БД после рестарта."""
        state = self._state.get(user_id)
        if state is not None:
            return state
        target = await self.store.get_target(user_id)
        if target is None:
            return None
        state = {"target": target, "phase": "text", "text": None}
        self._state[user_id] = state
        return state

    async def _reset(self, user_id: int) -> None:
        self._state.pop(user_id, None)
        await self.store.clear_target(user_id)

    async def _publish(self, message: discord.Message, state: dict, attachments: list[discord.Attachment]) -> None:
        """Публикует готовое объявление и закрывает мастер."""
        target: AnnounceTarget = state["target"]
        channel = None
        guild = self.bot.get_guild(target.guild_id)
        if guild is not None:
            channel = guild.get_channel(target.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target.channel_id)
            except (discord.HTTPException, discord.Forbidden, discord.NotFound):
                logger.warning("Канал %s объявления недоступен", target.channel_id, exc_info=True)
                channel = None
        if channel is None:
            await self._reset(message.author.id)
            await message.channel.send(
                embed=embeds.error("Канал недоступен", "Начните заново: напишите «старт» в личку бота.")
            )
            return

        if not state["text"] and not attachments:
            await self._reset(message.author.id)
            await message.channel.send(embed=embeds.error("Объявление пустое", "Отправка отменена."))
            return

        files = [await attachment.to_file() for attachment in attachments]
        try:
            await channel.send(content=state["text"], files=files or None)
        except discord.Forbidden:
            await self._reset(message.author.id)
            await message.channel.send(embed=embeds.error("Нет прав", "Бот не может писать в этот канал."))
            return
        except discord.HTTPException:
            logger.exception("Не удалось отправить объявление в %s", target.channel_id)
            return

        await self._reset(message.author.id)
        await message.channel.send(embed=embeds.success("Отправлено", f"Объявление опубликовано в **#{channel.name}**."))

    async def _ask_image(self, message: discord.Message) -> None:
        await message.channel.send(
            embed=embeds.info(
                "Шаг 4",
                "Пришлите **картинку** для объявления файлом\n"
                f"или отправьте `{SKIP}`, чтобы отправить без картинки.",
            )
        )

    @staticmethod
    def _is_skip(text: str) -> bool:
        return text.strip() == SKIP

    async def _handle_continue(self, message: discord.Message, state: dict) -> None:
        text = message.content.strip()

        if state["phase"] == "text":
            if self._is_skip(text):
                state["text"] = None
                if message.attachments:
                    await self._publish(message, state, list(message.attachments))
                    return
                state["phase"] = "image"
                await self._ask_image(message)
                return
            if not text:
                await message.channel.send(
                    embed=embeds.warning("Нужен текст", f"Напишите текст объявления или `{SKIP}`, чтобы пропустить его.")
                )
                return
            state["text"] = text
            if message.attachments:
                await self._publish(message, state, list(message.attachments))
                return
            state["phase"] = "image"
            await self._ask_image(message)
            return

        if self._is_skip(text):
            await self._publish(message, state, [])
            return
        if not message.attachments:
            await message.channel.send(
                embed=embeds.warning("Нужна картинка", f"Пришлите картинку файлом или `{SKIP}`, чтобы отправить без неё.")
            )
            return
        await self._publish(message, state, list(message.attachments))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not isinstance(message.channel, discord.DMChannel):
            return
        text = message.content.strip()

        if text.lower() in TRIGGERS:
            guilds = self._publishable_guilds(message.author.id)
            if not guilds:
                await message.channel.send(
                    embed=embeds.error("Нет прав", "Вы не администратор ни на одном сервере, где есть бот.")
                )
                return
            await self._reset(message.author.id)
            await message.channel.send(
                embed=embeds.brand("Панель отправки сообщений", "**Шаг 1:** Выберите сервер:"),
                view=GuildSelectView(guilds, self.store),
            )
            return

        state = await self._state_for(message.author.id)
        if state is None:
            return
        await self._handle_continue(message, state)

    # --- слэш-команда для гильдии ---

    @app_commands.command(name="announce", description="Отправить объявление от имени бота в канал")
    @app_commands.describe(channel="Канал (по умолчанию текущий)", text="Текст объявления")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def announce(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        text: str | None = None,
    ) -> None:
        if not text:
            await interaction.response.send_message(embed=embeds.error("Пусто", "Укажите текст объявления."), ephemeral=True)
            return
        target = channel or interaction.channel
        try:
            await target.send(text)
        except discord.Forbidden:
            await interaction.response.send_message(embed=embeds.error("Нет прав", "Бот не может писать в этот канал."), ephemeral=True)
            return
        logger.info("Объявление отправлено в %s пользователем %s", target.id, interaction.user.id)
        await interaction.response.send_message(
            embed=embeds.success("Отправлено", f"Объявление опубликовано в {target.mention}."),
            ephemeral=True,
        )
