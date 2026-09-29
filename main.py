"""Точка входа в приложение."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tracemalloc

import aiohttp
import discord

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

if os.getenv("RAM_REPORT_TRACEMALLOC", "1").strip().lower() not in ("0", "false", "no", "off"):
    tracemalloc.start()

from app.config import Config  # noqa: E402
from app.core.bot import MegaBot  # noqa: E402
from app.core.logger import setup_logging  # noqa: E402

logger = logging.getLogger("bot")

_APPLICATION_URL = "https://discord.com/developers/applications/{}/settings/interactions"
_INTENT_HINT = (
    "Откройте Developer Portal → Settings → Interactions и включите "
    "SERVER MEMBERS INTENT и MESSAGE CONTENT INTENT для этого приложения. "
    "Токен должен принадлежать именно этому приложению: ошибка почти всегда "
    "означает, что в .env лежит токен другого бота."
)


async def _preflight(config: Config) -> None:
    """Печатает, кто именно запускается, до подключения к шлюзу."""
    intents = discord.Intents.all()
    required = [
        name
        for name, value in (
            ("SERVER MEMBERS INTENT", intents.members),
            ("MESSAGE CONTENT INTENT", intents.message_content),
        )
        if value
    ]
    try:
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bot {config.token}"}
        ) as session:
            async with session.get("https://discord.com/api/v10/users/@me") as response:
                if response.status != 200:
                    logger.error(
                        "Токен отклонён Discord (HTTP %s). Проверьте BOT_TOKEN в .env.",
                        response.status,
                    )
                    return
                me = await response.json(content_type=None)
    except aiohttp.ClientError as error:
        logger.warning("Не удалось выполнить preflight-запрос к Discord: %s", error)
        return

    logger.info(
        "Запуск бота: %s#%s, application_id=%s, guild_id=%s",
        me.get("username"),
        me.get("discriminator") or "0",
        me.get("id"),
        config.guild_id or "не задан",
    )
    logger.info("Запрошенные privileged-интенты: %s", ", ".join(required))
    logger.info("Проверить настройки интентов: %s", _APPLICATION_URL.format(me.get("id")))


def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)

    bot = MegaBot(config)
    try:
        asyncio.run(_preflight(config))
    except Exception:  # noqa: BLE001
        logger.debug("preflight не удался", exc_info=True)

    try:
        bot.run(config.token)
    except discord.PrivilegedIntentsRequired:
        logger.error("Discord отклонил подключение: не включены privileged-интенты. %s", _INTENT_HINT)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    finally:
        logger.info("Завершение работы")


if __name__ == "__main__":
    main()
