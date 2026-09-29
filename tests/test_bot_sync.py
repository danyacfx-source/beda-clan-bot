"""Синхронизация команд: guild sync доступен сразу и не ждёт кеш сервера."""
from __future__ import annotations

import os
import tempfile

import pytest

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


def _bot(
    tree: _FakeTree | None = None,
    guild_id: int | None = 653949456630153216,
    panel_port: int | None = None,
) -> _SyncBot:
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(
            token="dummy",
            prefix="!",
            db_path=os.path.join(tmp, "bot.db"),
            log_level="ERROR",
            status_activity="test",
            owner_id=None,
            guild_id=guild_id,
            panel_port=panel_port,
        )
        return _SyncBot(config, tree or _FakeTree())


async def test_без_guild_id_синк_идёт_в_глобальный_список():
    tree = _FakeTree()
    bot = _bot(tree, guild_id=None)
    await bot._sync_commands()
    assert tree.synced_with == [None]
    assert tree.copied_to == []


async def test_команды_синхронизируются_в_гильдию_без_кеша():
    tree = _FakeTree()
    bot = _bot(tree)
    bot.get_guild = lambda _guild_id: None
    await bot._sync_commands()
    assert tree.copied_to == [653949456630153216]
    assert [item.id for item in tree.synced_with] == [653949456630153216]


async def test_on_ready_не_дублирует_guild_sync():
    tree = _FakeTree()
    bot = _bot(tree)
    await bot._sync_commands()
    await bot.on_ready()
    assert tree.copied_to == [653949456630153216]
    assert [item.id for item in tree.synced_with] == [653949456630153216]


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


class _BusyPortPanel:
    """Веб-панель, которая не смогла занять порт."""

    def __init__(self, _bot) -> None:
        pass

    async def start(self) -> None:
        # Windows и Linux отдают конфликт по порту как OSError 10048/98.
        raise OSError(10048, "адрес уже используется")


async def test_занятый_порт_панели_не_роняет_бота(monkeypatch):
    bot = _bot(panel_port=3000)
    monkeypatch.setattr("app.core.webpanel.WebPanel", _BusyPortPanel)

    await bot._start_webpanel()

    assert bot.webpanel is None


async def test_панель_включается_на_свободном_порту(monkeypatch):
    bot = _bot(panel_port=3000)
    started: list[object] = []

    class _Panel:
        def __init__(self, target) -> None:
            self._target = target

        async def start(self) -> None:
            started.append(self._target)

    monkeypatch.setattr("app.core.webpanel.WebPanel", _Panel)

    await bot._start_webpanel()

    assert started == [bot]
    assert bot.webpanel is not None


async def test_ошибка_безопасности_панели_остаётся_фатальной(monkeypatch):
    """Публичная панель без пароля обязана ронять старт, а не молча отключаться."""
    bot = _bot(panel_port=3000)

    class _InsecurePanel:
        def __init__(self, _bot) -> None:
            pass

        async def start(self) -> None:
            raise RuntimeError("PANEL_PASSWORD обязателен")

    monkeypatch.setattr("app.core.webpanel.WebPanel", _InsecurePanel)

    with pytest.raises(RuntimeError, match="PANEL_PASSWORD"):
        await bot._start_webpanel()


async def test_без_panel_port_панель_не_создаётся():
    bot = _bot()
    await bot._start_webpanel()
    assert bot.webpanel is None

