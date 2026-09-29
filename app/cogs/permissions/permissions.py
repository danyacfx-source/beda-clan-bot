"""Права категорий из конфига (порт permissions.js из Node)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from app.core.base import MegaCog

if TYPE_CHECKING:
    from app.core.bot import MegaBot

logger = logging.getLogger("bot.cogs")

_PERM_ATTR = {
    "view_channel": "view_channel",
    "send_messages": "send_messages",
    "read_message_history": "read_message_history",
    "attach_files": "attach_files",
    "embed_links": "embed_links",
    "connect": "connect",
    "speak": "speak",
    "manage_messages": "manage_messages",
    "manage_channels": "manage_channels",
    "manage_roles": "manage_roles",
}


def _parse_categories(raw: str) -> dict[str, dict[str, Any]]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("PERMISSIONS_CATEGORIES: невалидный JSON")
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _resolve_role(guild: discord.Guild, name: str) -> discord.Role | None:
    if name == "@everyone":
        return guild.default_role
    return discord.utils.get(guild.roles, name=name)


def _as_bool(value: Any) -> bool | None:
    """Приводит JSON-значение из конфига к bool (справляется со строками 'true'/'false')."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on", "да", "+"):  # noqa: SIM114
            return True
        if lowered in ("0", "false", "no", "off", "нет", "-"):
            return False
    return None


class PermissionsCog(MegaCog, name="Permissions"):
    def __init__(self, bot: MegaBot) -> None:
        super().__init__(bot)
        self._started = False

    def _categories(self) -> dict[str, dict[str, Any]]:
        return _parse_categories(self.bot.config.permissions_categories)

    @discord.app_commands.command(name="apply_permissions", description="Применить права категорий из конфига")
    @discord.app_commands.default_permissions(manage_guild=True)
    @discord.app_commands.guild_only()
    async def apply_permissions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        lines = await self._apply_all(interaction.guild)
        await interaction.followup.send(content="**Права категорий:**\n" + "\n".join(lines), ephemeral=True)

    @discord.app_commands.command(name="roles_required", description="Показать настройки прав категорий")
    @discord.app_commands.guild_only()
    async def roles_required(self, interaction: discord.Interaction) -> None:
        categories = self._categories()
        if not categories:
            await interaction.response.send_message("Права категорий из конфига не заданы.", ephemeral=True)
            return
        lines = []
        for cat_id, spec in categories.items():
            required = [rule.get("role") for rule in (spec.get("rules") or []) if rule.get("role")]
            lines.append(f"<#{cat_id}>: {', '.join(map(str, required))}")
        await interaction.response.send_message(content="**Права категорий:**\n" + "\n".join(lines), ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._started:
            return
        self._started = True
        if not self.bot.config.permissions_auto_apply or not self._categories():
            return
        guild_id = self.bot.config.guild_id
        if guild_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        for line in await self._apply_all(guild):
            logger.info("Права категорий %s: %s", guild.name, line)

    async def _apply_all(self, guild: discord.Guild) -> list[str]:
        lines: list[str] = []
        for cat_id, spec in self._categories().items():
            lines.append(await self._apply_category(guild, cat_id, spec))
        return lines

    async def _apply_category(self, guild: discord.Guild, cat_id: str, spec: dict[str, Any]) -> str:
        category = guild.get_channel(int(cat_id))
        if not isinstance(category, discord.CategoryChannel):
            return f"❌ Категория {cat_id} не найдена"
        try:
            for rule in spec.get("rules") or []:
                role = _resolve_role(guild, str(rule.get("role")))
                if role is None:
                    return f"❌ {category.name}: роль «{rule.get('role')}» не найдена"
                permissions: dict[str, bool] = {}
                for key, value in rule.items():
                    if key == "role":
                        continue
                    perm_name = _PERM_ATTR.get(key)
                    if perm_name is None:
                        logger.warning("Permissions: неизвестное право «%s» в категории %s", key, category.name)
                        continue
                    parsed = _as_bool(value)
                    if parsed is None:
                        continue
                    permissions[perm_name] = parsed
                if permissions:
                    overwrite = discord.PermissionOverwrite(**permissions)
                    await category.set_permissions(role, overwrite=overwrite, reason="Права категорий из конфига")
            return f"✅ {category.name}"
        except discord.Forbidden:
            return f"⛔ {category.name}: у бота нет прав"
        except (discord.HTTPException, TypeError, ValueError) as exc:
            return f"❌ {category.name}: {exc}"
