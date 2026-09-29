"""Правила-гейт: реакция ✅ на сообщении правил выдаёт роль участника."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.core.base import ClanCog

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

_TICK = "\u2705"


class RulesGateCog(ClanCog, name="RulesGate"):
    def __init__(self, bot: ClanBot) -> None:
        super().__init__(bot)

    async def cog_load(self) -> None:
        if self.bot.config.rules_message_id and self.bot.config.rules_role_id:
            self.bot.loop.create_task(self._prepare_gate())

    async def _prepare_gate(self) -> None:
        config = self.bot.config
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    message = await channel.fetch_message(config.rules_message_id)
                except discord.HTTPException:
                    continue
                if message is not None and config.rules_role_id:
                    role = guild.get_role(config.rules_role_id)
                    if role is None:
                        continue
                    reaction = discord.utils.get(message.reactions, emoji=_TICK)
                    if reaction is None:
                        await message.add_reaction(_TICK)
                    logger.info("RulesGate: гейт готов на %s", guild.name)
                return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        role = self._role_for(payload)
        if role is None:
            return
        member = self._member(payload)
        if member is None or role in member.roles:
            return
        try:
            await member.add_roles(role, reason="RulesGate: принял правила")
        except discord.HTTPException:
            logger.exception("RulesGate: не удалось выдать роль %s", member.id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        role = self._role_for(payload)
        if role is None:
            return
        member = self._member(payload)
        if member is None or role not in member.roles:
            return
        try:
            await member.remove_roles(role, reason="RulesGate: снял реакцию")
        except discord.HTTPException:
            logger.exception("RulesGate: не удалось снять роль %s", member.id)

    def _role_for(self, payload: discord.RawReactionActionEvent) -> discord.Role | None:
        config = self.bot.config
        emoji_name = getattr(payload.emoji, "name", str(payload.emoji))
        if emoji_name != _TICK or payload.message_id != config.rules_message_id or not config.rules_role_id:
            return None
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return None
        return guild.get_role(config.rules_role_id)

    def _member(self, payload: discord.RawReactionActionEvent) -> discord.Member | None:
        if payload.guild_id is None:
            return None
        guild = self.bot.get_guild(payload.guild_id)
        return guild.get_member(payload.user_id) if guild is not None else None
