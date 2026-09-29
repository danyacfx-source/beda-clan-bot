"""Тесты панели заявок в клан: дефолты настроек и лимиты Discord."""
from __future__ import annotations

import os

import pytest

from app.core import embeds
from app.core.ticket_content import (
    CLAN_INTRO_FIELDS,
    CLAN_INTRO_FOOTER,
    CLAN_INTRO_INLINE,
    CLAN_INTRO_TEXT,
    CLAN_INTRO_TITLE,
    CLAN_PANEL_BUTTON,
    CLAN_PANEL_EMOJI,
    CLAN_PANEL_FOOTER,
    CLAN_PANEL_TEXT,
    CLAN_PANEL_TITLE,
)
from app.db.database import Database
from app.db.settings_repository import SettingsRepository


@pytest.fixture
async def repo(tmp_path):
    database = Database(os.path.join(tmp_path, "test.db"))
    await database.connect()
    try:
        yield SettingsRepository(database)
    finally:
        await database.close()


async def test_новые_настройки_получают_панель_клан(repo):
    settings = await repo.get(999)
    assert settings["ticket_panel_title"] == CLAN_PANEL_TITLE
    assert settings["ticket_panel_description"] == CLAN_PANEL_TEXT
    assert settings["ticket_panel_footer"] == CLAN_PANEL_FOOTER
    assert settings["ticket_open_label"] == CLAN_PANEL_BUTTON
    assert settings["ticket_open_emoji"] == CLAN_PANEL_EMOJI
    assert settings["ticket_intro_title"] == CLAN_INTRO_TITLE
    assert settings["ticket_intro_description"] == CLAN_INTRO_TEXT
    assert settings["ticket_intro_footer"] == CLAN_INTRO_FOOTER


async def test_существующая_строка_не_перезатирается(repo):
    await repo.ensure_row(1)
    await repo.set(1, "ticket_panel_title", "Моё название")
    await repo.ensure_row(1)
    assert (await repo.get(1))["ticket_panel_title"] == "Моё название"


def test_текст_панели_в_лимитах_discord():
    assert len(CLAN_PANEL_TITLE) <= 256
    assert len(CLAN_PANEL_TEXT) <= 4096
    assert len(CLAN_PANEL_FOOTER) <= 2048
    assert len(CLAN_PANEL_BUTTON) <= 80
    assert "ЗАЯВКА В КЛАН" in CLAN_PANEL_TITLE
    assert "Расскажи немного о себе" in CLAN_PANEL_BUTTON


def _intro_embed():
    embed = embeds.brand(
        CLAN_INTRO_TITLE,
        CLAN_INTRO_TEXT.replace("{member}", "<@1>"),
        CLAN_INTRO_FOOTER,
    )
    for index, (name, value) in enumerate(CLAN_INTRO_FIELDS):
        embed.add_field(name=name, value=value, inline=index < CLAN_INTRO_INLINE)
    return embed


def test_интро_тикета_содержит_вопросы_для_собеседования():
    assert [name for name, _ in CLAN_INTRO_FIELDS] == [
        "🎂 Возраст",
        "👤 Имя",
        "🎮 Steam ID",
        "📍 Город",
        "🕐 Удобное время для собеса",
    ]


def test_интро_тикета_уложено_в_лимиты_discord():
    embed = _intro_embed()
    assert embed.title == CLAN_INTRO_TITLE
    assert "<@1>" in (embed.description or "")
    assert len(embed.description or "") <= 4096
    assert len(embed.footer.text or "") <= 2048
    assert len(embed.fields) <= 25
    assert [field.inline for field in embed.fields] == [True] * 4 + [False]
    for field in embed.fields:
        assert len(field.name) <= 256
        assert 0 < len(field.value) <= 1024
