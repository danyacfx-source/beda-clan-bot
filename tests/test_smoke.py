"""Smoke-тест: полная сборка бота без подключения к Discord."""
import os
import tempfile

import pytest

from app.config import Config
from app.core.bot import MegaBot


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
        bot = MegaBot(config)
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
