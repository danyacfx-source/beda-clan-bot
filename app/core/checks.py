"""Проверки прав, иерархические правила и фабрика app-командных чеков."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot.checks")


def bot_has_permissions(**perms: bool):
    """App-команда исполнится только если у бота есть все требуемые права на гильдии.

    При нехватке прав поднимается _BotMissingPermissions и пользователю объясняется,
    каких именно прав не хватает у бота.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return True
        required = [name for name, needed in perms.items() if needed]
        missing = [name for name in required if not getattr(interaction.guild.me.guild_permissions, name, False)]
        if missing:
            from app.core.bot import _BotMissingPermissions

            raise _BotMissingPermissions(missing)
        return True

    return app_commands.check(predicate)


def is_owner() -> app_commands.check:
    """Доступно только владельцу бота из настройки OWNER_ID."""

    async def predicate(interaction: discord.Interaction) -> bool:
        owner_id = getattr(interaction.client, "config", None) and interaction.client.config.owner_id
        if owner_id and interaction.user.id == owner_id:
            return True
        raise commands.NotOwner("Эта команда доступна владельцу бота.")

    return app_commands.check(predicate)


def requires_role(attribute: str, extra_role_ids: Callable[[discord.Interaction], Awaitable[set[int]]] | None = None) -> app_commands.check:
    """Пропускает участников с ролями из конфига и, опционально, с ролями раздела.

    Проверка аддитивна: достаточно любого одного из источников. Иначе роль,
    выданная через /setup_where_play, оказывалась отрезана внешней проверкой,
    хотя внутренняя её допускала, и команда отвечала «Недостаточно прав» тому,
    кому доступ положен.

    Администраторы проходят всегда: иначе ограничение по конкретной роли
    заперло бы снаружи команды того, кто может её настроить. Пустой список
    в конфиге означает «без ограничений», чтобы опечатка в настройке не
    закрыла команду целиком.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        config = getattr(interaction.client, "config", None)
        role_ids = set(getattr(config, attribute, ()) or ()) if config is not None else set()
        if extra_role_ids is not None:
            # Роль раздела из БД: внешний чек обязан её учитывать, иначе он
            # ужесточает доступ относительно is_manager внутри команды.
            try:
                role_ids |= set(await extra_role_ids(interaction))
            except Exception:
                logger.warning("Не удалось прочитать роль раздела «Где играем» для проверки прав", exc_info=True)
        if not role_ids or interaction.guild is None:
            return True
        member = interaction.user
        if member.guild_permissions.administrator:
            return True
        if {role.id for role in getattr(member, "roles", ())} & role_ids:
            return True
        names = []
        for role_id in sorted(role_ids):
            role = interaction.guild.get_role(role_id)
            if role is None:
                logger.warning("Роль %s из %s не найдена на сервере %s", role_id, attribute, interaction.guild_id)
                continue
            names.append(discord.utils.escape_markdown(role.name))
        hint = ", ".join(names) if names else "роль в конфигурации не найдена на сервере"
        raise app_commands.CheckFailure(f"Команда доступна только участникам с ролями: {hint}.")

    return app_commands.check(predicate)


def can_moderate(me: discord.Member, target: discord.Member) -> bool:
    """Можно ли боту модернировать участника с учётом иерархии ролей."""
    if target.id == me.id or target.bot:
        return False
    if target.id == me.guild.owner_id:
        return False
    if target.top_role.position >= me.top_role.position:
        return False
    return True


def moderation_reason(author: discord.Member | discord.User, extra: str = "") -> str:
    parts = [f"Инициатор: {author} ({author.id})"]
    if extra:
        parts.append(extra)
    return " | ".join(parts)
