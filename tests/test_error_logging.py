"""Тесты логирования ошибок бота (app/core/bot.py).

Проверяем, что неизвестные ошибки команд пишутся с трейсбеком в лог,
а известные (права, кулдаун) — только предупреждением без шума.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

from discord import app_commands
from discord.ext import commands

from app.config import Config
from app.core.bot import MegaBot

_bot_logger = "bot"


def _bot() -> MegaBot:
    bot = MegaBot.__new__(MegaBot)
    bot.config = Config(
        token="x",
        prefix="!",
        db_path=":memory:",
        log_level="ERROR",
        status_activity="s",
        owner_id=None,
    )
    bot.services = None
    return bot


def _interaction(command_name: str = "testcmd"):
    interaction = MagicMock()
    command = MagicMock()
    command.qualified_name = command_name
    interaction.command = command
    interaction.guild = None
    interaction.channel = None
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_unknown_error_logs_with_traceback(caplog):
    bot = _bot()
    interaction = _interaction()
    error = RuntimeError("boom")
    error.__traceback__ = None
    with caplog.at_level(logging.ERROR, logger=_bot_logger):
        await bot.on_app_command_error(interaction, error)
    assert any(rec.levelname == "ERROR" for rec in caplog.records)
    assert any("Ошибка команды /testcmd" in rec.getMessage() for rec in caplog.records)
    assert any("boom" in rec.getMessage() for rec in caplog.records)


async def test_known_error_logs_warning_only(caplog):
    bot = _bot()
    interaction = _interaction()
    error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 60.0), retry_after=42)
    error.__traceback__ = None
    with caplog.at_level(logging.WARNING, logger=_bot_logger):
        await bot.on_app_command_error(interaction, error)
    assert any(rec.levelname == "WARNING" for rec in caplog.records)
    assert any("Ошибка команды /testcmd" in rec.getMessage() for rec in caplog.records)
    assert not any(rec.levelname == "ERROR" for rec in caplog.records)
    interaction.response.send_message.assert_awaited_once()


async def test_not_owner_error_is_user_facing(caplog):
    bot = _bot()
    interaction = _interaction()
    error = commands.NotOwner("owner only")
    with caplog.at_level(logging.WARNING, logger=_bot_logger):
        await bot.on_app_command_error(interaction, error)

    interaction.response.send_message.assert_awaited_once()
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert embed.title == "Только для владельца"
    assert not any(rec.levelname == "ERROR" for rec in caplog.records)
