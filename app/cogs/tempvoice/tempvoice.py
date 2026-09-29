"""Временные голосовые каналы: триггер-канал создаёт личный VC с панелью управления."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.core import embeds
from app.core.base import ClanCog
from app.services.temp_voice_service import TempVoiceService

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")


class RenameVoiceModal(discord.ui.Modal, title="Переименовать канал"):
    name_input = discord.ui.TextInput(label="Новое название", max_length=40)

    def __init__(self, channel: discord.VoiceChannel) -> None:
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.channel.edit(name=self.name_input.value.strip()[:40] or "комната")
        except discord.HTTPException:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Не удалось переименовать."), ephemeral=True)
            return
        await interaction.response.send_message(embed=embeds.success("Готово", f"Канал переименован в «{self.name_input.value[:40]}»."))


class LimitVoiceModal(discord.ui.Modal, title="Лимит участников"):
    limit_input = discord.ui.TextInput(label="Лимит (0 — без лимита)", max_length=3)

    def __init__(self, channel: discord.VoiceChannel) -> None:
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.limit_input.value.strip()
        try:
            limit = int(raw)
        except ValueError:
            limit = 0
        limit = 0 if limit < 0 else min(limit, 99)
        try:
            await self.channel.edit(user_limit=limit)
        except discord.HTTPException:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Не удалось изменить лимит."), ephemeral=True)
            return
        text = "без лимита" if limit == 0 else f"{limit}"
        await interaction.response.send_message(embed=embeds.success("Готово", f"Лимит участников: **{text}**."))


class MemberSelectView(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel, kind: str, service: TempVoiceService) -> None:
        super().__init__(timeout=60)
        self.kind = kind
        self.service = service
        members = [member for member in channel.members if member.bot is False][:25]
        select = discord.ui.Select(
            placeholder="Выберите участника",
            options=[discord.SelectOption(label=member.display_name[:80], value=str(member.id)) for member in members],
        )
        select.callback = self._callback
        self.add_item(select)

    async def _callback(self, interaction: discord.Interaction) -> None:
        select = self.children[0]
        if not isinstance(select, discord.ui.Select) or not select.values:
            await interaction.response.edit_message(content="Выберите участника.", view=None)
            return
        member = interaction.guild.get_member(int(select.values[0])) if interaction.guild else None
        if member is None:
            await interaction.response.edit_message(content="Участник не найден.", view=None)
            return
        if self.kind == "kick":
            try:
                await member.move_to(None, reason="TempVoice: выгнан владельцем")
            except discord.HTTPException:
                await interaction.response.edit_message(content="Не удалось выгнать.", view=None)
                return
            await interaction.response.edit_message(content=f"🚫 Выгнан: {member.display_name}", view=None)
        elif self.kind == "transfer":
            await self.service.transfer(self.channel.id, member.id)
            await self._rebuild_panel(interaction, member)
            await interaction.response.edit_message(content=f"👑 Владелец передан: {member.display_name}", view=None)

    async def _rebuild_panel(self, interaction: discord.Interaction, new_owner: discord.Member) -> None:
        if isinstance(interaction.message, discord.Message):
            try:
                await interaction.message.delete()
            except discord.HTTPException:
                pass
        try:
            await self.channel.send("⭐ Панель управления каналом", view=TempVoicePanelView(new_owner.id, self.service))
        except discord.HTTPException:
            logger.debug("TempVoice: не удалось пересоздать панель", exc_info=True)


class TempVoicePanelView(discord.ui.View):
    def __init__(self, owner_id: int, service: TempVoiceService) -> None:
        super().__init__(timeout=None)
        self.owner_id = owner_id
        self.service = service

    async def _deny(self, interaction: discord.Interaction) -> None:
        embed = embeds.error("Только владелец", "Управлять каналом может только его создатель.")
        try:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass

    async def _owner_channel(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        if isinstance(interaction.channel, discord.VoiceChannel):
            return interaction.channel
        if interaction.guild is None:
            return None
        channel_id = await self.service.channel_of_owner(interaction.user.id)
        if channel_id is None:
            return None
        channel = interaction.guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.VoiceChannel) else None

    @discord.ui.button(label="Переименовать", emoji="✏️", style=discord.ButtonStyle.primary)
    async def rename_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await self._deny(interaction)
            return
        channel = await self._owner_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                embed=embeds.warning("Нет канала", "Вы не находитесь во временном канале."), ephemeral=True
            )
            return
        await interaction.response.send_modal(RenameVoiceModal(channel))

    @discord.ui.button(label="Лимит", emoji="👥", style=discord.ButtonStyle.primary)
    async def limit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await self._deny(interaction)
            return
        channel = await self._owner_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                embed=embeds.warning("Нет канала", "Вы не находитесь во временном канале."), ephemeral=True
            )
            return
        await interaction.response.send_modal(LimitVoiceModal(channel))

    @discord.ui.button(label="Выгнать", emoji="🚫", style=discord.ButtonStyle.danger)
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await self._deny(interaction)
            return
        channel = await self._owner_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                embed=embeds.warning("Нет канала", "Вы не находитесь во временном канале."), ephemeral=True
            )
            return
        if not channel.members:
            await interaction.response.send_message(embed=embeds.warning("Пусто", "В канале никого нет."), ephemeral=True)
            return
        await interaction.response.send_message("Кого выгнать?", view=MemberSelectView(channel, "kick", self.service), ephemeral=True)

    @discord.ui.button(label="Передать", emoji="👑", style=discord.ButtonStyle.secondary)
    async def transfer_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await self._deny(interaction)
            return
        channel = await self._owner_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                embed=embeds.warning("Нет канала", "Вы не находитесь во временном канале."), ephemeral=True
            )
            return
        if not channel.members:
            await interaction.response.send_message(embed=embeds.warning("Пусто", "Передавать владельца некому."), ephemeral=True)
            return
        await interaction.response.send_message(
            "Кому передать владение?",
            view=MemberSelectView(channel, "transfer", self.service),
            ephemeral=True,
        )

    @discord.ui.button(label="Удалить", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await self._deny(interaction)
            return
        channel = await self._owner_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                embed=embeds.warning("Нет канала", "Вы не находитесь во временном канале."), ephemeral=True
            )
            return
        await self.service.delete(channel.id)
        try:
            await channel.delete(reason="TempVoice: удалён владельцем")
        except discord.HTTPException:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Не удалось удалить канал."), ephemeral=True)
            return
        await interaction.response.send_message(embed=embeds.success("Удалено", "Канал удалён."))
        self.stop()


class TempVoiceCog(ClanCog, name="TempVoice"):
    def __init__(self, bot: ClanBot, tempvoice: TempVoiceService) -> None:
        super().__init__(bot)
        self.tempvoice = tempvoice

    def _legacy_config(self) -> dict[str, Any]:
        config = self.bot.config
        return {
            "category_id": getattr(config, "temp_voice_category_id", None),
            "trigger_ids": tuple(getattr(config, "temp_voice_trigger_ids", ())),
        }

    async def _tempvoice_config(self, guild_id: int | None) -> dict[str, Any]:
        try:
            return await self.module_config(guild_id, "tempvoice")
        except (AttributeError, RuntimeError, TypeError):
            return self._legacy_config()

    async def cog_load(self) -> None:
        tempconf = await self._tempvoice_config(self.bot.config.guild_id)
        if tempconf["trigger_ids"]:
            self.cleanup_loop.start()

    async def cog_unload(self) -> None:
        self.cleanup_loop.cancel()

    @app_commands.command(name="temp_panel", description="Отправить панель управления временными каналами")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def temp_panel(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Панель можно отправить только в текстовый канал."),
                ephemeral=True,
            )
            return
        embed = embeds.brand(
            "Управление голосовым каналом",
            "Зайдите в созданный вами временный канал и выберите действие.\n\n"
            "`01`  **Переименовать** · изменить название\n"
            "`02`  **Лимит** · ограничить участников\n"
            "`03`  **Выгнать** · отключить участника\n"
            "`04`  **Передать** · назначить владельца\n"
            "`05`  **Удалить** · закрыть канал",
            footer="BEDA  •  управление голосовым каналом",
        )
        await interaction.response.send_message(embed=embed, view=TempVoicePanelView(interaction.user.id, self.tempvoice))

    @tasks.loop(seconds=60.0)
    async def cleanup_loop(self) -> None:
        for row in await self.tempvoice.all():
            channel = self.bot.get_channel(row["channel_id"])
            if isinstance(channel, discord.VoiceChannel) and not channel.members:
                await self._remove_channel(channel.id)

    async def _remove_channel(self, channel_id: int) -> None:
        """Удаляет временный голосовой канал из Discord и из БД."""
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return
        try:
            await channel.delete(reason="TempVoice: пустой канал убран")
        except discord.HTTPException:
            logger.debug("TempVoice: не удалось удалить канал %s", channel_id, exc_info=True)
            return
        await self.tempvoice.delete(channel_id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        before_channel = before.channel
        if isinstance(before_channel, discord.VoiceChannel) and not before_channel.members:
            owner = await self.tempvoice.owner_of(before_channel.id)
            if owner is not None:
                await self._remove_channel(before_channel.id)

        tempconf = await self._tempvoice_config(after.guild.id if after.guild else None)
        trigger = after.channel if after.channel and after.channel.id in tempconf["trigger_ids"] else None
        if trigger is None:
            return
        existing_id = await self.tempvoice.channel_of_owner(member.id)
        if existing_id is not None:
            existing = self.bot.get_channel(existing_id)
            if isinstance(existing, discord.VoiceChannel):
                try:
                    await member.move_to(existing)
                except discord.HTTPException:
                    logger.debug("TempVoice: не удалось переместить в существующий канал", exc_info=True)
                return
        try:
            category = self.bot.get_channel(tempconf["category_id"]) if tempconf["category_id"] else None
            if category is None or not isinstance(category, discord.CategoryChannel):
                category = trigger.category
            channel = await trigger.guild.create_voice_channel(
                member.display_name[:30],
                category=category,
                reason="TempVoice: создан участником",
            )
            await self.tempvoice.create(member.id, channel.id, datetime.now(UTC))
            await member.move_to(channel)
            await channel.send("⭐ Панель управления каналом", view=TempVoicePanelView(member.id, self.tempvoice))
        except discord.HTTPException:
            logger.exception("TempVoice: не удалось создать канал для %s", member.id)
