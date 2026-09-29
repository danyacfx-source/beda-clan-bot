"""Синхронизация команд: глобальный список не должен стирать команды гильдии."""
from __future__ import annotations

import os
import tempfile

from app.config import Config
from app.core.bot import ClanBot


class _FakeTree:
    def __init__(self) -> None:
        self.synced_with: list[object] = []
        self.copied_to: list[int] = []

    async def sync(self, *, guild=None):
        # Как в discord.py 2.5.2: sync тоже ждёт Snowflake, а не int.
        if guild is not None and not hasattr(guild, "id"):
            raise TypeError("sync() ждёт объект Snowflake, а не идентификатор")
        self.synced_with.append(guild)
        return []

    async def fetch_commands(self):
        return []

    def copy_global_to(self, *, guild):
        # Как в discord.py 2.5.2: синхронный метод, принимает Snowflake.
        # Возвращает None, поэтому случайный await здесь сразу падает.
        if not hasattr(guild, "id"):
            raise TypeError("copy_global_to() ждёт объект Snowflake, а не идентификатор")
        self.copied_to.append(guild.id)
        return None


class _SyncBot(ClanBot):
    """ClanBot с уже известным application_id — как после login()."""

    def __init__(self, config: Config, tree: _FakeTree) -> None:
        # tree нужен до super().__init__: конструктор клиента читает self.tree.
        self._fake_tree = tree
        super().__init__(config)

    @property
    def user(self):
        return object()

    @property
    def application_id(self):
        return 1

    @property
    def tree(self):
        return self._fake_tree


def _bot(tree: _FakeTree | None = None, guild_id: int | None = 653949456630153216) -> _SyncBot:
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(
            token="dummy",
            prefix="!",
            db_path=os.path.join(tmp, "bot.db"),
            log_level="ERROR",
            status_activity="test",
            owner_id=None,
            guild_id=guild_id,
        )
        return _SyncBot(config, tree or _FakeTree())


async def test_синк_идёт_только_в_глобальный_список():
    """Гильдейский sync отправил бы пустой список и стёр бы команды сервера."""
    tree = _FakeTree()
    bot = _bot(tree, guild_id=None)
    await bot._sync_commands()
    assert tree.synced_with == [None]
    assert tree.copied_to == []


class _FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


async def test_глобальные_команды_копируются_в_гильдию():
    """Гильдейская копия применяется мгновенно, глобальная — до часа.

    Копирование живёт в on_ready, а не в setup_hook: setup_hook выполняется
    внутри login() до подключения к шлюзу, когда гильдий в кэше ещё нет.
    """
    tree = _FakeTree()
    bot = _bot(tree)
    guild = _FakeGuild(653949456630153216)
    bot.get_guild = lambda guild_id: guild if guild_id == guild.id else None
    await bot.on_ready()
    assert tree.copied_to == [guild.id]
    assert tree.synced_with == [guild]


async def test_копирование_в_гильдию_не_повторяется_при_reconnect():
    tree = _FakeTree()
    bot = _bot(tree)
    guild = _FakeGuild(653949456630153216)
    bot.get_guild = lambda _guild_id: guild
    await bot.on_ready()
    await bot.on_ready()
    assert tree.copied_to == [guild.id]


async def test_без_гильдии_в_кэше_копирование_пропускается():
    tree = _FakeTree()
    bot = _bot(tree)
    bot.get_guild = lambda _guild_id: None
    await bot.on_ready()
    assert tree.copied_to == []


async def test_до_логина_синк_пропускается():
    tree = _FakeTree()
    bot = _bot(tree)

    class _LoggedOut(_SyncBot):
        @property
        def user(self):
            return None

    bot.__class__ = _LoggedOut
    await bot._sync_commands()
    assert tree.synced_with == []
