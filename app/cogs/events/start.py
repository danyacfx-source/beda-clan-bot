"""Старт бота: статус, прогрев настроек, проверка окружения."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.core.base import MegaCog
from app.core.presence import bot_activity
from app.services.settings_service import SettingsService

if TYPE_CHECKING:
    from app.core.bot import MegaBot

logger = logging.getLogger("bot")


class StartCog(MegaCog, name="Lifecycle"):
    def __init__(self, bot: MegaBot, settings: SettingsService) -> None:
        super().__init__(bot)
        self.settings = settings

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        logger.info("Бот %s готов, серверов: %d", self.bot.user, len(self.bot.guilds))
        await self.settings.ensure_all_guilds(self.bot)
        self._warn_on_missing_tools()
        try:
            await self.bot.change_presence(activity=bot_activity(self.bot.config.status_activity))
        except Exception:
            logger.debug("Не удалось установить статус", exc_info=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.settings.ensure_all_guilds(self.bot)
        logger.info("Добавлен на сервер %s (%d)", guild.name, guild.id)

    def _warn_on_missing_tools(self) -> None:
        try:
            if not discord.opus.is_loaded():
                try:
                    discord.opus.load_opus()
                except Exception as exc:
                    if not discord.opus.is_loaded():
                        candidates = (
                            "opus",
                            "libopus",
                            "libopus.so.0",
                            "libopus.so",
                            "libopus-0.x64.dll",
                        )
                        ok = False
                        for cand in candidates:
                            try:
                                discord.opus.load_opus(cand)
                            except Exception:
                                continue
                            ok = True
                            logger.info("libopus загружен из %s", cand)
                            break
                        if not ok:
                            logger.warning("libopus не загружен — голосовые каналы не работают: %s", exc)
        except Exception as exc:
            logger.warning("Не удалось загрузить libopus: %s", exc)
