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
        self.synced_with.append(guild)
        return []

    async def fetch_commands(self):
        return []

    async def copy_global_to(self, *, guild):
        # Сигнатура повторяет discord.py: аргумент только keyword.
        if not isinstance(guild, int):
            raise TypeError("copy_global_to() принимает только keyword-аргумент guild")
        self.copied_to.append(guild)
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
    bot = _bot(tree)
    bot.get_guild = lambda _guild_id: None
    await bot._sync_commands()
    assert tree.synced_with == [None]
    assert tree.copied_to == []


async def test_без_guild_id_быстрые_команды_не_копируются():
    tree = _FakeTree()
    bot = _bot(tree, guild_id=None)
    await bot._sync_commands()
    assert tree.synced_with == [None]
    assert tree.copied_to == []


async def test_глобальные_команды_копируются_в_гильдию():
    """Гильдейская копия применяется мгновенно, глобальная — до часа."""
    tree = _FakeTree()
    bot = _bot(tree)
    bot.get_guild = lambda _guild_id: object()
    await bot._sync_commands()
    assert tree.synced_with == [None, 653949456630153216]
    assert tree.copied_to == [653949456630153216]


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
