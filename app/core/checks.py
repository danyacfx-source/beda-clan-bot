"""Проверки прав, иерархические правила и фабрика app-командных чеков."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


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
