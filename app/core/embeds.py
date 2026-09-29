"""Единая визуальная система Discord-сообщений бота «Асуна Юки»."""

from __future__ import annotations

from datetime import UTC, datetime

import discord

BRAND = discord.Color(0xFF5A36)
SUCCESS = discord.Color(0x3ECF8E)
ERROR = discord.Color(0xFF4D67)
INFO = discord.Color(0x6C7CFF)
WARNING = discord.Color(0xFFB547)
NEUTRAL = discord.Color(0x272D3A)
BOT_NAME = "Асуна Юки"


def _base(
    color: discord.Color,
    title: str,
    description: str | None = None,
    footer: str | None = None,
    *,
    timestamp: bool = True,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    if timestamp:
        embed.timestamp = datetime.now(UTC)
    embed.set_footer(text=footer or f"{BOT_NAME}  •  Discord bot")
    return embed


def success(title: str, description: str | None = None, footer: str | None = None, *, timestamp: bool = True) -> discord.Embed:
    return _base(SUCCESS, title, description, footer, timestamp=timestamp)


def error(title: str, description: str | None = None, footer: str | None = None, *, timestamp: bool = True) -> discord.Embed:
    return _base(ERROR, title, description, footer, timestamp=timestamp)


def info(title: str, description: str | None = None, footer: str | None = None, *, timestamp: bool = True) -> discord.Embed:
    return _base(INFO, title, description, footer, timestamp=timestamp)


def warning(title: str, description: str | None = None, footer: str | None = None, *, timestamp: bool = True) -> discord.Embed:
    return _base(WARNING, title, description, footer, timestamp=timestamp)


def brand(title: str, description: str | None = None, footer: str | None = None, *, timestamp: bool = True) -> discord.Embed:
    return _base(BRAND, title, description, footer, timestamp=timestamp)


def neutral(title: str, description: str | None = None, footer: str | None = None, *, timestamp: bool = True) -> discord.Embed:
    return _base(NEUTRAL, title, description, footer, timestamp=timestamp)
