"""Тесты структуры после перехода: composition root вместо DI-контейнера.

Старые контейнерные тесты (build/resolve/cycle/DENIED) утратили смысл —
их место заняли инварианты новой сборки: явный граф, синглтоны,
инъекция когов, статическая граница слоёв.
"""
import importlib
import inspect
import os
import pkgutil
import tempfile

import pytest
from discord.ext import commands

from app.cogs import COGS_PACKAGE
from app.config import Config
from app.core.bot import MegaBot
from app.core.composition import assemble
from app.core.loader import COG_PROVIDERS, _build_cog
from app.db.database import Database
from app.services.reminder_service import ReminderService
from app.services.settings_service import SettingsService


def _config(tmp: str) -> Config:
    return Config(
        token="x",
        prefix="!",
        db_path=os.path.join(tmp, "bot.db"),
        log_level="ERROR",
        status_activity="s",
        owner_id=None,
    )


def test_assemble_builds_full_graph_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "bot.db"))
        root = assemble(config=_config(tmp), db=db)

        # Все сервисы — реальные классы из app/.
        assert root.services.settings.__class__ is SettingsService
        assert root.services.reminders.__class__ is ReminderService

        # Синглтоны: один и тот же объект прошит во всех потребителей.
        assert root.services.tickets._settings is root.services.settings
        assert root.services.logging._settings is root.services.settings
        assert root.services.moderation._settings is root.services.settings

        # bot и root связаны одним и тем же объектом services.
        assert root.bot is not None
        assert root.bot.services is root.services


def test_cogs_never_accept_repositories() -> None:
    """Статическая граница слоёв: параметры когов = bot + ключи COG_PROVIDERS."""
    package = importlib.import_module(COGS_PACKAGE)
    modules = sorted(
        (m for m in pkgutil.walk_packages(package.__path__, prefix=f"{COGS_PACKAGE}.") if not m.ispkg),
        key=lambda m: m.name,
    )
    allowed = {"bot"} | set(COG_PROVIDERS)
    checked = 0
    for module in modules:
        mod = importlib.import_module(module.name)
        for member_name, member in vars(mod).items():
            if not (isinstance(member, type) and issubclass(member, commands.Cog)):
                continue
            if member is commands.Cog or member.__module__ != mod.__name__:
                continue
            params = list(inspect.signature(member.__init__).parameters.values())[1:]
            for p in params:
                assert p.name in allowed or p.default is not inspect.Parameter.empty, (
                    f"ког {member.__name__} тянет за контейнер: {p.name}"
                )
            checked += 1
    assert checked >= 20, f"проверено когов: {checked}"


@pytest.mark.asyncio
async def test_setup_hook_injects_cog_with_services() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bot = MegaBot(_config(tmp))
        await bot.setup_hook()
        try:
            # bot.services собран через composition root.
            assert isinstance(bot.services.settings, SettingsService)
            assert bot.root.bot is bot
            assert bot.services.logging._bot is bot

            # Loader отдал когу тот же синглтон, что лежит в services.
            setup = bot.get_cog("Setup")
            assert setup is not None
            assert setup.settings is bot.services.settings
        finally:
            await bot.close()


@pytest.mark.asyncio
async def test_all_cogs_load_through_composition() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bot = MegaBot(_config(tmp))
        await bot.setup_hook()
        try:
            loaded = bot.cogs
            assert len(loaded) >= 20, f"когов загружено: {len(loaded)}"
            assert {"Setup", "Moderation", "AutoMod", "Reminders", "TempVoice"} <= set(loaded)

            from app.cogs.general.help import _CATEGORIES, _category_embed, _flatten

            entries = _flatten(bot)
            assert entries
            for category in _CATEGORIES:
                # Discord ограничивает description эмбеда 4096 символами.
                assert len(_category_embed(category, entries).description or "") <= 4096
        finally:
            await bot.close()


def test_build_cog_fallback_uses_constructor_defaults() -> None:
    """Ког без сервисов строится через cls(bot): поведение не потеряно с контейнером."""
    from app.cogs.general.ping import PingCog

    with tempfile.TemporaryDirectory() as tmp:
        bot = MegaBot(_config(tmp))
        cog = _build_cog(bot, PingCog)
        assert isinstance(cog, PingCog)
