"""Настройка каналов: логи, категория тикетов + настройка ролей и каналов из конфига."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import MegaCog
from app.core.setup_data import CHANNEL_CATEGORIES, EXTRA_ROLES, ROLE_SETTINGS
from app.services.settings_service import SettingsService

if TYPE_CHECKING:
    from app.core.bot import MegaBot

setup_group = app_commands.Group(name="setup", description="Настройка сервера")

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

_LOG_COLUMNS = {
    "bot": ("bot_log_channel_id", "Бот (запуск)"),
    "member": ("member_log_channel_id", "Участники"),
    "message": ("message_log_channel_id", "Сообщения"),
    "voice": ("voice_log_channel_id", "Голосовые каналы"),
    "mod": ("mod_log_channel_id", "Модерация и баны"),
}

_PERM_NAMES = {
    "CreateInstantInvite": "create_instant_invite",
    "KickMembers": "kick_members",
    "BanMembers": "ban_members",
    "Administrator": "administrator",
    "ManageChannels": "manage_channels",
    "ManageGuild": "manage_guild",
    "AddReactions": "add_reactions",
    "ViewAuditLog": "view_audit_log",
    "PrioritySpeaker": "priority_speaker",
    "Stream": "stream",
    "ViewChannel": "view_channel",
    "SendMessages": "send_messages",
    "SendTTSMessages": "send_tts_messages",
    "ManageMessages": "manage_messages",
    "EmbedLinks": "embed_links",
    "AttachFiles": "attach_files",
    "ReadMessageHistory": "read_message_history",
    "MentionEveryone": "mention_everyone",
    "UseExternalEmojis": "use_external_emojis",
    "ViewGuildInsights": "view_guild_insights",
    "Connect": "connect",
    "Speak": "speak",
    "MuteMembers": "mute_members",
    "DeafenMembers": "deafen_members",
    "MoveMembers": "move_members",
    "UseVAD": "use_voice_activation",
    "ChangeNickname": "change_nickname",
    "ManageNicknames": "manage_nicknames",
    "ManageRoles": "manage_roles",
    "ManageWebhooks": "manage_webhooks",
    "ManageEmojisAndStickers": "manage_emojis_and_stickers",
    "UseApplicationCommands": "use_application_commands",
    "RequestToSpeak": "request_to_speak",
    "ManageEvents": "manage_events",
    "ManageThreads": "manage_threads",
    "CreatePublicThreads": "create_public_threads",
    "CreatePrivateThreads": "create_private_threads",
    "SendMessagesInThreads": "send_messages_in_threads",
    "UseExternalStickers": "use_external_stickers",
}


class SetupCog(MegaCog, name="Setup"):
    def __init__(self, bot: MegaBot, settings: SettingsService) -> None:
        super().__init__(bot)
        self.settings = settings

    @setup_group.command(name="log-channel", description="Канал для логов событий и модерации")
    @app_commands.guild_only()
    async def log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self.settings.update(interaction.guild.id, log_channel_id=channel.id)
        await interaction.response.send_message(embed=embeds.success("Настройка", f"Логи → {channel.mention}"))

    @setup_group.command(name="log-type", description="Отдельный канал для конкретного типа логов")
    @app_commands.guild_only()
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Бот (запуск)", value="bot"),
            app_commands.Choice(name="Участники", value="member"),
            app_commands.Choice(name="Сообщения", value="message"),
            app_commands.Choice(name="Голосовые каналы", value="voice"),
            app_commands.Choice(name="Модерация и баны", value="mod"),
        ]
    )
    async def log_type(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        column, label = _LOG_COLUMNS[kind.value]
        await self.settings.update(interaction.guild.id, **{column: channel.id})
        await interaction.response.send_message(embed=embeds.success("Настройка", f"{label} → {channel.mention}"))

    @setup_group.command(name="ticket-category", description="Категория для создания тикетов")
    @app_commands.guild_only()
    async def ticket_category(self, interaction: discord.Interaction, category: discord.CategoryChannel) -> None:
        await self.settings.update(interaction.guild.id, ticket_category_id=category.id)
        await interaction.response.send_message(embed=embeds.success("Настройка", f"Тикеты → {category.mention}"))

    @setup_group.command(name="unset", description="Сбросить настройку канала")
    @app_commands.describe(option="Какую настройку сбросить")
    @app_commands.guild_only()
    async def unset(self, interaction: discord.Interaction, option: str) -> None:
        mapping = {
            "log": "log_channel_id",
            "ticket": "ticket_category_id",
            "bot": "bot_log_channel_id",
            "member": "member_log_channel_id",
            "message": "message_log_channel_id",
            "voice": "voice_log_channel_id",
            "mod": "mod_log_channel_id",
        }
        column = mapping.get(option.strip().lower())
        if column is None:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Ошибка",
                    "Варианты: `log`, `ticket`, `bot`, `member`, `message`, `voice`, `mod`.",
                ),
                ephemeral=True,
            )
            return
        await self.settings.update(interaction.guild.id, **{column: None})
        await interaction.response.send_message(embed=embeds.success("Сброшено", f"`{column}` → пусто."))

    @setup_group.command(name="show", description="Текущие настройки сервера")
    @app_commands.guild_only()
    async def show(self, interaction: discord.Interaction) -> None:
        settings = await self.settings.get(interaction.guild.id)

        def mention(value, kind: discord.ChannelType | None = None):
            return f"<#{value}>" if value else "—"

        embed = embeds.info("Настройки сервера")
        embed.add_field(name="Канал логов", value=mention(settings["log_channel_id"]), inline=True)
        embed.add_field(name="Категория тикетов", value=mention(settings["ticket_category_id"]), inline=True)
        embed.add_field(
            name="Логи: бот",
            value=mention(settings.get("bot_log_channel_id")),
            inline=True,
        )
        embed.add_field(
            name="Логи: участники",
            value=mention(settings.get("member_log_channel_id")),
            inline=True,
        )
        embed.add_field(
            name="Логи: сообщения",
            value=mention(settings.get("message_log_channel_id")),
            inline=True,
        )
        embed.add_field(
            name="Логи: голос",
            value=mention(settings.get("voice_log_channel_id")),
            inline=True,
        )
        embed.add_field(
            name="Логи: модерация",
            value=mention(settings.get("mod_log_channel_id")),
            inline=True,
        )
        embed.add_field(name="Авто-модерация", value="включена ✅" if settings["automod_enabled"] else "выключена ❌", inline=True)
        await interaction.response.send_message(embed=embed)


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", "-", name.lower())


def _find_channel(channels, name: str) -> discord.abc.GuildChannel | None:
    target = _norm_name(name)
    for channel in channels:
        if _norm_name(channel.name) == target:
            return channel
    return None


def _parse_color(value) -> int | None:
    if isinstance(value, int):
        return value
    match = _HEX_RE.match(str(value).strip())
    if match:
        return int(match.group(1), 16)
    try:
        return int(str(value), 16)
    except ValueError:
        return None


def _apply_permission_flags(permissions: discord.Permissions, specs) -> None:
    if not specs:
        return
    for spec in specs:
        if isinstance(spec, str):
            attr = _PERM_NAMES.get(spec)
            if attr:
                setattr(permissions, attr, True)
        elif isinstance(spec, dict):
            attr = _PERM_NAMES.get(str(spec.get("name", "")))
            if attr:
                setattr(permissions, attr, spec.get("allowed", True))
    return None


class ServerSetupCog(MegaCog, name="ServerSetup"):
    """Автонастройка сервера по конфигу (порт setup.js)."""

    async def _bot_top_role(self, guild: discord.Guild) -> discord.Role | None:
        member = guild.me
        if member is None:
            return None
        try:
            me = await guild.fetch_member(member.id)
        except discord.HTTPException:
            me = member
        return me.top_role

    async def _apply_role_settings(self, guild: discord.Guild, bot_top: discord.Role) -> list[str]:
        reports: list[str] = []
        ordered = sorted(ROLE_SETTINGS.items(), key=lambda item: int(str(item[1].get("order") or 999)))
        position = bot_top.position - 1

        for name, spec in ordered:
            role = discord.utils.get(guild.roles, name=name)
            if role is None:
                reports.append(f"⚠️ **{name}** — не создана")
                continue
            edit_kwargs: dict[str, object] = {}
            if spec.get("color"):
                color = _parse_color(spec["color"])
                if color is None:
                    reports.append(f"❌ **{name}** — неверный цвет")
                    continue
                edit_kwargs["color"] = discord.Color(color)
            if spec.get("permissions"):
                flags = discord.Permissions(role.permissions.value)
                _apply_permission_flags(flags, spec["permissions"])
                edit_kwargs["permissions"] = flags
            if position >= bot_top.position:
                reports.append(f"❌ **{name}** — позиция выше/равна роли бота, пропуск")
                continue
            if position > 1:
                edit_kwargs["position"] = position
            if spec.get("hoist") is not None:
                edit_kwargs["hoist"] = bool(spec["hoist"])
            if spec.get("mentionable") is not None:
                edit_kwargs["mentionable"] = bool(spec["mentionable"])
            try:
                await role.edit(reason="Настройка ролей из конфига", **edit_kwargs)
                reports.append(f"✅ **{name}** — цвет и позиция {position}")
            except discord.Forbidden:
                reports.append(f"⛔ **{name}** — нет прав на изменение")
            except discord.HTTPException as exc:
                reports.append(f"❌ **{name}** — {exc}")
            position -= 1
        return reports

    async def _sponsor_roles(self, guild: discord.Guild, spec: dict[str, Any]) -> list[discord.Role]:
        roles: list[discord.Role] = []
        for name in spec.get("roles") or []:
            role = discord.utils.get(guild.roles, name=name)
            if role is not None:
                roles.append(role)
        return roles

    async def _apply_sponsor_permissions(self, channel: discord.abc.GuildChannel, sponsor_roles: list[discord.Role]) -> None:
        guild = channel.guild
        await channel.set_permissions(guild.default_role, view_channel=False)
        for role in sponsor_roles:
            await channel.set_permissions(role, view_channel=True)

    @app_commands.command(name="setup_roles", description="Создать все роли из конфига и настроить их")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup_roles(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        bot_top = await self._bot_top_role(guild)
        if bot_top is None:
            await interaction.followup.send("Бот не найден на сервере.", ephemeral=True)
            return

        created: list[str] = []
        existing: list[str] = []
        for name in list(EXTRA_ROLES):
            role = discord.utils.get(guild.roles, name=name)
            if role is not None:
                existing.append(name)
                continue
            try:
                await guild.create_role(name=name, reason="Настройка ролей ботом")
                created.append(name)
            except discord.HTTPException:
                pass

        reports = await self._apply_role_settings(guild, bot_top)
        report_lines = "\n".join(reports) if reports else "нет настроек в конфиге"
        await interaction.followup.send(
            f"**Создано ролей:** {', '.join(created) if created else 'нет (все уже есть)'}\n"
            f"**Уже существовали:** {', '.join(existing) if existing else 'нет'}\n\n"
            f"**Настройки:**\n{report_lines}",
            ephemeral=True,
        )

    @app_commands.command(name="apply_role_settings", description="Применить цвет, порядок и права ролей из конфига")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def apply_role_settings(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        bot_top = await self._bot_top_role(guild)
        if bot_top is None:
            await interaction.followup.send("Бот не найден на сервере.", ephemeral=True)
            return
        reports = await self._apply_role_settings(guild, bot_top)
        await interaction.followup.send(
            "**Применены настройки ролей:**\n" + ("\n".join(reports) if reports else "нет настроек в конфиге"),
            ephemeral=True,
        )

    @app_commands.command(name="setup_channels", description="Создать категории и каналы из конфига")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup_channels(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        created: list[str] = []
        existing: list[str] = []

        for category_name, spec in CHANNEL_CATEGORIES.items():
            category = discord.utils.get(guild.categories, name=category_name)
            created_category = category is None
            if category is None:
                try:
                    category = await guild.create_category(category_name, reason="Настройка каналов ботом")
                except discord.HTTPException:
                    continue

            sponsor_roles: list[discord.Role] = []
            if spec.get("type") == "sponsor":
                sponsor_roles = await self._sponsor_roles(guild, spec)

            category_type = spec.get("type")
            if category_type == "temp":
                create_name = str(spec.get("create") or "➕ Создать канал")
                channel = _find_channel([c for c in category.channels if isinstance(c, discord.VoiceChannel)], create_name)
                if channel is not None:
                    existing.append(f"🔊 {create_name}")
                else:
                    try:
                        await category.create_voice_channel(create_name, reason="Настройка каналов ботом")
                        created.append(f"🔊 {create_name}")
                    except discord.HTTPException:
                        pass
                continue

            text_value = spec.get("text_channels") if category_type == "sponsor" else spec.get("channels")
            voice_value = spec.get("voice_channels")
            text_names = [str(item) for item in text_value] if isinstance(text_value, list) else []
            voice_names = [str(item) for item in voice_value] if isinstance(voice_value, list) else []

            for name in text_names:
                channel = _find_channel([c for c in category.channels if isinstance(c, discord.TextChannel)], name)
                if channel is not None:
                    existing.append(f"#{name}")
                    continue
                try:
                    channel = await category.create_text_channel(name, reason="Настройка каналов ботом")
                    if sponsor_roles:
                        await self._apply_sponsor_permissions(channel, sponsor_roles)
                    created.append(f"#{name}")
                except discord.HTTPException:
                    pass

            for name in voice_names:
                channel = _find_channel([c for c in category.channels if isinstance(c, discord.VoiceChannel)], name)
                if channel is not None:
                    existing.append(f"🔊 {name}")
                    continue
                try:
                    channel = await category.create_voice_channel(name, reason="Настройка каналов ботом")
                    if sponsor_roles:
                        await self._apply_sponsor_permissions(channel, sponsor_roles)
                    created.append(f"🔊 {name}")
                except discord.HTTPException:
                    pass

            if created_category:
                created.append(f"Категория «{category_name}»")

        await interaction.followup.send(
            f"**Создано:** {', '.join(created) if created else 'нет (всё уже есть)'}\n"
            f"**Уже существовали:** {', '.join(existing) if existing else 'нет'}",
            ephemeral=True,
        )

    @app_commands.command(name="debug_channels", description="Показать текущие категории и каналы")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def debug_channels(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        lines: list[str] = []
        for category in sorted(guild.categories, key=lambda c: c.name):
            sub: list[str] = []
            for channel in sorted(category.text_channels, key=lambda c: c.name):
                sub.append(f"# {channel.name} ({channel.id})")
            for channel in sorted(category.voice_channels, key=lambda c: c.name):
                sub.append(f"🔊 {channel.name} ({channel.id})")
            lines.append(f"**{category.name}** ({category.id}): " + (", ".join(sub) if sub else "— пусто"))

        for channel in sorted(guild.text_channels, key=lambda c: c.name):
            if channel.category_id is None:
                lines.append(f"# {channel.name} (без категории)")
        for channel in sorted(guild.voice_channels, key=lambda c: c.name):
            if channel.category_id is None:
                lines.append(f"🔊 {channel.name} (без категории)")

        message = "\n".join(lines) if lines else "Нет каналов."
        if len(message) > 1900:
            message = message[:1900]
        await interaction.followup.send(f"**Каналы сервера:**\n{message}", ephemeral=True)
