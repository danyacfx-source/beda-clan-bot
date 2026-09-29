"""Тесты новых фич веб-панели: автомод (БД-слова), опросы, настройки автомода.

Гарантируют, что вкладки «Автомод» и «Опросы» опираются на живые методы
сервисов, а стоп-слова из БД реально блокируются фильтром.
"""
import os
from types import SimpleNamespace

from app.config import Config
from app.core.composition import assemble
from app.db.database import Database

GUILD_ID = 4321


def _config(tmp: str) -> Config:
    return Config(
        token="x",
        prefix="!",
        db_path=os.path.join(tmp, "bot.db"),
        log_level="ERROR",
        status_activity="s",
        owner_id=None,
    )


async def _setup(tmp: str):
    db = Database(os.path.join(tmp, "bot.db"))
    await db.connect()
    root = assemble(config=_config(tmp), db=db)
    return root.services, db


# --- автомод: _analyze с учётом БД-слов ---

def _automod_cog(env_banned: str = "") -> object:
    from app.cogs.moderation.automod import AutoModCog

    cfg = SimpleNamespace(
        automod_banned_words=env_banned,
        automod_block_links=False,
        automod_allowed_links="",
        automod_caps_threshold=0.8,
        automod_caps_min_len=12,
        automod_max_messages_in_window=0,
    )
    cog = AutoModCog.__new__(AutoModCog)
    cog.bot = SimpleNamespace(config=cfg)
    cog._messages = {}
    cog._timeout_counts = {}
    return cog


class TestAutomodAnalyze:
    def test_db_blocked_word_triggers(self):
        cog = _automod_cog()
        reason = cog._analyze(1, "ты просто гад", ["гад"])
        assert reason is not None and "гад" in reason

    def test_env_and_db_words_merge(self):
        cog = _automod_cog(env_banned="дурак,злой")
        assert "дурак" in cog._analyze(1, "какой же ты дурак", ["гад"])
        assert "гад" in cog._analyze(1, "вот это гад", ["гад"])

    def test_clean_message_passes(self):
        cog = _automod_cog()
        assert cog._analyze(1, "всё хорошо, продолжаем", ["гад"]) is None


# --- опросы ---

class TestPollsService:
    async def test_create_list_vote_end(self, tmp_path):
        services, db = await _setup(str(tmp_path))
        try:
            polls = services.polls
            poll_id = await polls.create(GUILD_ID, 100, 42, "Лучшая игра?", ["Raid", "Сюжетная кампания"])
            await polls.bind_message(poll_id, 9000)

            assert await polls.vote(poll_id, 7, 0, 2) == 2
            assert await polls.vote(poll_id, 8, 0, 2) == 2
            assert await polls.vote(poll_id, 9, 1, 2) == 2
            assert await polls.vote(poll_id, 7, 0, 2) == 0  # голос не менялся

            rows = await polls.list_for_guild(GUILD_ID)
            assert len(rows) == 1
            assert rows[0]["question"] == "Лучшая игра?"
            assert rows[0]["message_id"] == 9000
            assert await polls.vote_counts(poll_id) == {0: 2, 1: 1}

            question, options, counts = await polls.end(poll_id)
            assert question == "Лучшая игра?"
            assert options == ["Raid", "Сюжетная кампания"]
            assert sum(counts.values()) == 3

            row = await polls.get(poll_id)
            assert row["active"] == 0
        finally:
            await db.close()


# --- настройки автомода (то, что пишет вкладка) ---

class TestAutomodSettings:
    async def test_toggle_and_blocked_words_persist(self, tmp_path):
        services, db = await _setup(str(tmp_path))
        try:
            settings = services.settings
            first = await settings.get(GUILD_ID)
            assert first.get("automod_enabled", True) is True

            await settings.update(GUILD_ID, automod_enabled=0)
            assert (await settings.get(GUILD_ID))["automod_enabled"] is False

            words = await settings.set_blocked_words(GUILD_ID, [" гад ", "гад", "ТАРАКАН"])
            assert words == ["гад", "таракан"]
            assert await settings.blocked_words(GUILD_ID) == ["гад", "таракан"]
        finally:
            await db.close()
