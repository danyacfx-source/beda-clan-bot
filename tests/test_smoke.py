"""Smoke-тест: полная сборка бота без подключения к Discord."""
import importlib
import os
import tempfile

import pytest

from app.config import Config
from app.core.bot import ClanBot


def test_entrypoint_imports_existing_bot_class():
    """main.py обязан импортировать то же имя класса, что и app.core.bot.

    Точка входа лежит в корне, а не в app/, поэтому её легко не заметить при
    переименовании: тест ловит рассинхрон на локальной машине, а не на сервере.
    """
    entrypoint = importlib.import_module("main")
    assert entrypoint.ClanBot is ClanBot


def test_no_legacy_brand_in_python_sources():
    """Старое имя не должно оставаться нигде в импортируемом коде."""
    import app

    # Имена собираются из кусков, иначе проверка нашла бы саму себя.
    legacy_names = ("Mega" + "Bot", "Mega" + "Cog", "mega" + "bot_")
    root = os.path.dirname(os.path.dirname(os.path.abspath(app.__file__)))
    checked = 0
    for folder in ("app", "tests", "scripts"):
        directory = os.path.join(root, folder)
        for current, _, files in os.walk(directory):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(current, name)
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
                checked += 1
                for legacy in legacy_names:
                    assert legacy not in text, f"{legacy} остался в {name}"
    with open(os.path.join(root, "main.py"), encoding="utf-8") as handle:
        entrypoint_source = handle.read()
    checked += 1
    for legacy in legacy_names:
        assert legacy not in entrypoint_source, f"{legacy} остался в main.py"
    assert checked > 50


@pytest.mark.asyncio
async def test_bot_bootstrap():
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(
            token="dummy-token",
            prefix="!",
            db_path=os.path.join(tmp, "bot.db"),
            log_level="ERROR",
            status_activity="test",
            owner_id=None,
        )
        bot = ClanBot(config)
        try:
            await bot.setup_hook()
            assert bot.db is not None
            assert bot.services is not None
            cog_names = {c.__class__.__name__ for c in bot.cogs.values()}
            assert len(cog_names) >= 20, f"Мало когов: {sorted(cog_names)}"
            for expected in (
                "GiveawaysCog",
                "PollsCog",
                "RemindersCog",
                "SnipeCog",
                "UtilityCog",
                "WarnsCog",
                "RamReportCog",
                "TempVoiceCog",
                "TicketCog",
                "EventsCog",
                "WherePlayCog",
                "DossierCog",
            ):
                assert expected in cog_names, f"Не загружен ког {expected}"

            # Портированные команды видны в дереве.
            command_names = {command.name for command in bot.tree.get_commands()}
            for expected in (
                "event",
                "event_edit",
                "event_cancel",
                "event_list",
                "event_signup",
                "setup_where_play",
                "where_play",
                "where_play_status",
                "where_play_card",
                "stop_play",
                "setup_dossiers",
                "review_dossier",
                "approve_interview",
            ):
                assert expected in command_names, f"Нет команды /{expected}"

            assert hasattr(bot.services, "events")
            assert hasattr(bot.services, "where_play")
            assert hasattr(bot.services, "dossiers")

            # Медийные подсистемы удалены из клан-сборки.
            assert not hasattr(bot.services, "music")
            assert not hasattr(bot.services, "donations")
            assert not hasattr(bot.services, "twitch")
            assert not hasattr(bot.services, "kick")
            assert not hasattr(bot.services, "seasons")
            assert not hasattr(bot.services, "reaction_roles")
            assert not hasattr(bot, "overlay")
        finally:
            await bot.close()

        assert bot.db is None
