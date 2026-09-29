"""Авто-модерация как на Node automod.js (спам/слова/ссылки/капс/растяжки)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.core.base import ClanCog
from app.services.settings_service import SettingsService

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

_INVITE_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_HOST_RE = re.compile(r"//(?:www\.)?([^/]+)", re.IGNORECASE)
_STRETCH_RE = re.compile(r"(.)\1{5,}", re.IGNORECASE)

_SPAM_WINDOW = 5.0


def _normalized_allowed_links(raw: str) -> list[str]:
    hosts: list[str] = []
    for link in (part.strip().lower() for part in raw.split(",") if part.strip()):
        host = link
        if host.startswith(("http://", "https://")):
            host = re.sub(r"^https?://", "", host)
        host = host.rstrip("/").split(":")[0]
        if host:
            hosts.append(host)
    return hosts


def _blocked_link_host(content: str, allowed_hosts: list[str]) -> str | None:
    for match in _INVITE_URL_RE.findall(content):
        host_match = _HOST_RE.search(match)
        if not host_match:
            continue
        host = host_match.group(1).strip().split(":")[0].split("?")[0].split("#")[0].rstrip(".")
        if not host:
            continue
        if not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
            return host
    return None


def _is_caps(content: str, threshold: float = 0.8, min_len: int = 12) -> bool:
    if len(content) < min_len:
        return False
    stripped = _INVITE_URL_RE.sub("", content)
    alpha = re.sub(r"[^a-zA-Zа-яА-ЯёЁ]", "", stripped)
    if len(alpha) < min_len:
        return False
    upper = sum(1 for ch in alpha if ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")
    return upper / len(alpha) >= threshold if alpha else False


def _is_stretched(content: str) -> bool:
    return bool(_STRETCH_RE.search(content))


class AutoModCog(ClanCog, name="AutoMod"):
    def __init__(self, bot: ClanBot, settings: SettingsService) -> None:
        super().__init__(bot)
        self.settings = settings
        self._messages: dict[int, list[float]] = {}
        self._timeout_counts: dict[int, list[float]] = {}
        self._joins: dict[int, list[float]] = {}
        self._raid_until: dict[int, float] = {}
        self._raid_restore_tasks: dict[int, asyncio.Task[None]] = {}
        self._lockdown_tasks: dict[int, asyncio.Task[None]] = {}

    async def cog_unload(self) -> None:
        for task in self._raid_restore_tasks.values():
            task.cancel()
        for task in self._lockdown_tasks.values():
            task.cancel()
        self._raid_restore_tasks.clear()
        self._lockdown_tasks.clear()

    async def activate_lockdown(self, guild: discord.Guild, seconds: int | None = None) -> int:
        """Запрещает сообщения @everyone и автоматически восстанавливает права."""
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            return 0
        old: list[tuple[discord.TextChannel, discord.PermissionOverwrite]] = []
        for channel in guild.text_channels:
            current = channel.overwrites_for(guild.default_role)
            if current.send_messages is False:
                continue
            updated = current.copy()
            updated.send_messages = False
            try:
                await channel.set_permissions(guild.default_role, overwrite=updated, reason="Automod: lockdown")
                old.append((channel, current))
            except discord.HTTPException:
                logger.debug("Automod: не удалось закрыть %s", channel, exc_info=True)
        duration = max(30, seconds or self.bot.config.automod_lockdown_seconds)
        prior = self._lockdown_tasks.pop(guild.id, None)
        if prior is not None:
            prior.cancel()

        async def restore() -> None:
            await asyncio.sleep(duration)
            for channel, overwrite in old:
                try:
                    await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Automod: lockdown завершён")
                except discord.HTTPException:
                    pass
            self._lockdown_tasks.pop(guild.id, None)

        self._lockdown_tasks[guild.id] = asyncio.create_task(restore())
        logger.warning("Automod: lockdown включён на %s на %dс", guild, duration)
        return len(old)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        config = self.bot.config
        if member.bot or member.guild is None:
            return
        if config.automod_min_account_age_days > 0:
            created = member.created_at
            if datetime.now(UTC) - created < timedelta(days=config.automod_min_account_age_days):
                me = member.guild.me
                if me is not None and me.guild_permissions.kick_members:
                    try:
                        await member.kick(reason="Automod: слишком новый аккаунт")
                        logger.warning("Automod: кик нового аккаунта %s", member)
                    except discord.HTTPException:
                        logger.warning("Automod: не удалось удалить новый аккаунт %s", member, exc_info=True)
                return
        if not config.automod_antiraid_enabled:
            return
        now = time.monotonic()
        joins = self._joins.setdefault(member.guild.id, [])
        window = config.automod_antiraid_window_seconds
        joins[:] = [stamp for stamp in joins if now - stamp <= window]
        joins.append(now)
        if len(joins) >= config.automod_antiraid_join_threshold:
            await self._activate_raid_mode(member.guild)

    async def _activate_raid_mode(self, guild: discord.Guild) -> None:
        config = self.bot.config
        now = time.monotonic()
        if self._raid_until.get(guild.id, 0.0) > now:
            return
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            logger.warning("Automod: anti-raid сработал, но нет manage_channels в %s", guild)
            return
        self._raid_until[guild.id] = now + config.automod_antiraid_cooldown_seconds
        changed: list[tuple[discord.TextChannel, int]] = []
        for channel in guild.text_channels:
            if channel.id in set(config.automod_ignored_channels):
                continue
            previous = channel.slowmode_delay
            target = max(previous, config.automod_antiraid_slowmode_seconds)
            if target == previous:
                continue
            try:
                await channel.edit(slowmode_delay=target, reason="Automod: anti-raid")
                changed.append((channel, previous))
            except discord.HTTPException:
                logger.debug("Automod: не удалось включить slowmode в %s", channel, exc_info=True)
        logger.warning("Automod: anti-raid режим включён на сервере %s (%d каналов)", guild, len(changed))

        async def restore() -> None:
            await asyncio.sleep(config.automod_antiraid_cooldown_seconds)
            for channel, previous in changed:
                try:
                    await channel.edit(slowmode_delay=previous, reason="Automod: anti-raid завершён")
                except discord.HTTPException:
                    pass
            self._raid_until.pop(guild.id, None)
            self._raid_restore_tasks.pop(guild.id, None)

        task = asyncio.create_task(restore())
        self._raid_restore_tasks[guild.id] = task

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.bot.config.automod_enabled:
            return
        if message.author.bot or message.guild is None or isinstance(message.channel, discord.DMChannel):
            return

        settings = await self.settings.get(message.guild.id)
        if not settings.get("automod_enabled", True):
            return

        member = message.author
        if isinstance(member, discord.User):
            member = message.guild.get_member(member.id)
        if member is None or not isinstance(member, discord.Member):
            return
        if self._has_ignored_role(member):
            return
        if member.guild_permissions.manage_messages:
            return
        config = self.bot.config
        if message.channel.id in set(config.automod_ignored_channels):
            return

        self._track_spam(member.id)
        blocked_words = await self.settings.blocked_words(message.guild.id)
        reason = self._analyze(member.id, message.content or "", blocked_words)
        if not reason:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            pass
        await self._punish(member, reason)
        channel_name = getattr(message.channel, "name", message.channel.id)
        logger.warning("Automod: %s в #%s: %s", message.author, channel_name, reason)

    def _has_ignored_role(self, member: discord.Member) -> bool:
        ignored = set(self.bot.config.automod_ignore_roles)
        return bool(ignored and any(role.name in ignored for role in member.roles))

    def _analyze(self, user_id: int, content: str, blocked_words: list[str] | None = None) -> str | None:
        if self._is_spam(user_id):
            return "спам"
        lowered = content.lower()
        exempt = getattr(self.bot.config, "automod_exempt_regex", "")
        if exempt:
            try:
                if re.search(exempt, content, flags=re.IGNORECASE):
                    return None
            except re.error:
                logger.error("Некорректный AUTOMOD_EXEMPT_REGEX", exc_info=True)

        banned: set[str] = {w.strip() for w in (blocked_words or []) if w and w.strip()}
        for raw in (self.bot.config.automod_banned_words or "").split(","):
            word = raw.strip()
            if word:
                banned.add(word)
        for word in banned:
            if word and word in lowered:
                return f"запрещённое слово: «{word}»"

        config = self.bot.config
        if config.automod_block_links:
            allowed = _normalized_allowed_links(config.automod_allowed_links)
            host = _blocked_link_host(lowered, allowed)
            if host:
                return f"ссылка на неразрешённый домен: {host}"
        if _is_caps(content, config.automod_caps_threshold, config.automod_caps_min_len):
            return "капс"
        if _is_stretched(content):
            return "растянутый спам"
        return None

    def _track_spam(self, user_id: int) -> None:
        now = time.monotonic()
        stamps = self._messages.setdefault(user_id, [])
        while stamps and now - stamps[0] > _SPAM_WINDOW:
            stamps.pop(0)
        stamps.append(now)
        while len(stamps) > 20:
            stamps.pop(0)

    def _is_spam(self, user_id: int) -> bool:
        max_in_window = self.bot.config.automod_max_messages_in_window
        if max_in_window <= 0:
            return False
        stamps = self._messages.get(user_id, [])
        now = time.monotonic()
        recent = [stamp for stamp in stamps if now - stamp <= _SPAM_WINDOW]
        return len(stamps) > max_in_window or len(recent) > max_in_window

    async def _punish(self, member: discord.Member, reason: str) -> None:
        config = self.bot.config
        duration = config.automod_timeout_seconds
        if duration > 0:
            try:
                await member.timeout(discord.utils.utcnow() + timedelta(seconds=duration), reason=f"Automod: {reason}")
            except discord.HTTPException:
                pass

        ban_after = config.automod_ban_after_timeouts
        if ban_after > 0:
            now = time.monotonic()
            window = config.automod_ban_window_seconds
            stamps = self._timeout_counts.setdefault(member.id, [])
            while stamps and now - stamps[0] > window:
                stamps.pop(0)
            stamps.append(now)
            if len(stamps) >= ban_after:
                try:
                    await member.ban(reason=f"Automod: {ban_after} нарушений за {window}с")
                    logger.warning("Automod: бан %s (%s)", member, reason)
                except discord.HTTPException:
                    pass
                stamps.clear()
