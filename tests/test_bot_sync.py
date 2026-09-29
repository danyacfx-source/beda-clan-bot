"""Синхронизация команд: глобальный список не должен стирать команды гильдии."""
from __future__ import annotations

import os
import tempfile

from app.config import Config
from app.core.bot import ClanBot


class _FakeTree:
    def __init__(self) -> None:
        self.synced_with: list[object] = []

    async def sync(self, *, guild=None):
        self.synced_with.append(guild)
        return []

    async def fetch_commands(self):
        return []


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


def _bot(tree: _FakeTree | None = None) -> _SyncBot:
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(
            token="dummy",
            prefix="!",
            db_path=os.path.join(tmp, "bot.db"),
            log_level="ERROR",
            status_activity="test",
            owner_id=None,
            guild_id=653949456630153216,
        )
        return _SyncBot(config, tree or _FakeTree())


async def test_синк_идёт_только_в_глобальный_список():
    """Гильдейный sync отправил бы пустой список и стёр бы команды сервера."""
    tree = _FakeTree()
    bot = _bot(tree)
    await bot._sync_commands()
    assert tree.synced_with == [None]


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
