"""Счётчики сервера в голосовых каналах (порт server_stats.js из Node)."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import tasks

from app.core.base import ClanCog

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

_FALLBACK_EMOJI = {"members": "👥", "online": "🟢"}


def _parse_channels(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("SERVER_STATS_CHANNELS: невалидный JSON")
        return []
    return data if isinstance(data, list) else []


def _clean_emoji(raw: str | None, fallback: str) -> str:
    if not raw or not raw.strip():
        return fallback
    value = raw.strip()
    codepoint = ord(value[0])
    if codepoint > 0x2FFF:
        return value
    return fallback


class ServerStatsCog(ClanCog, name="ServerStats"):
    def __init__(self, bot: ClanBot) -> None:
        super().__init__(bot)
        self._started = False
        self._presence_cache: dict[int, float] = {}
        self._last_rename: dict[int, float] = {}

    _RENAME_MIN_INTERVAL = 360.0

    async def cog_load(self) -> None:
        if self.bot.config.server_stats_enabled:
            self.update_loop.change_interval(seconds=self.bot.config.server_stats_update_seconds)
            self.update_loop.start()

    async def cog_unload(self) -> None:
        self.update_loop.cancel()

    @tasks.loop(seconds=300.0)
    async def update_loop(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._update_guild(guild)
            except Exception:
                logger.exception("ServerStats: не удалось обновить счётчики сервера %s", guild.name)

    @update_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _update_guild(self, guild: discord.Guild) -> None:
        config = self.bot.config
        channels_cfg = _parse_channels(config.server_stats_channels)
        if not channels_cfg:
            return
        category = guild.get_channel(config.server_stats_category_id) if config.server_stats_category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.get(guild.categories, name=config.server_stats_category_name)
            if category is None:
                try:
                    category = await guild.create_category(config.server_stats_category_name, reason="Счётчики сервера")
                except discord.HTTPException:
                    logger.warning("ServerStats: нет прав создать категорию в %s", guild.name)
                    return

        members = guild.member_count or 0
        online = await self._online_count(guild)

        counter_channels: list[discord.VoiceChannel] = []
        for channel in category.voice_channels:
            counter_channels.append(channel)
        counter_channels.sort(key=lambda c: c.position)

        for index, spec in enumerate(channels_cfg):
            kind = spec.get("type") or "members"
            value = online if kind == "online" else members
            emoji = _clean_emoji(spec.get("emoji"), _FALLBACK_EMOJI.get(kind, ""))
            name = (f"{emoji} {value}" if emoji else str(value))[:100]
            channel = counter_channels[index] if index < len(counter_channels) else None
            if channel is None:
                try:
                    channel = await guild.create_voice_channel(name, category=category, reason="Счётчик сервера")
                    counter_channels.append(channel)
                except discord.HTTPException:
                    logger.warning("ServerStats: нет прав создать канал счётчика в %s", guild.name)
                    continue
            if channel.name != name:
                now = time.monotonic()
                if now - self._last_rename.get(channel.id, 0.0) < self._RENAME_MIN_INTERVAL:
                    continue
                try:
                    await channel.edit(name=name, reason="Обновление счётчика сервера")
                    self._last_rename[channel.id] = time.monotonic()
                except discord.HTTPException:
                    logger.warning("ServerStats: нет прав переименовать канал в %s", guild.name)

        for extra in counter_channels[len(channels_cfg) :]:
            try:
                await extra.delete(reason="Очистка лишних счётчиков")
                logger.info("ServerStats: удалён лишний счётчик %s (%s)", extra.name, extra.id)
            except discord.HTTPException:
                logger.warning("ServerStats: нет прав удалить лишний счётчик %s", extra.name)

    async def _online_count(self, guild: discord.Guild) -> int:
        if guild.approximate_presence_count is not None:
            return guild.approximate_presence_count
        now = time.monotonic()
        cached = self._presence_cache.get(guild.id, 0.0)
        count = 0
        if now - cached > 300:
            self._presence_cache[guild.id] = now
            async for member in guild.fetch_members():
                if not member.bot and member.status != discord.Status.offline:
                    count += 1
            return count if count else sum(1 for member in guild.members if not member.bot and member.status != discord.Status.offline)
        return sum(1 for member in guild.members if not member.bot and member.status != discord.Status.offline)
