"""Тесты порта «Где играем»: код подключения, снапшот-API, карточка, репозиторий."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import Config
from app.db.database import Database
from app.db.where_play_repository import WherePlayRepository
from app.services.where_play_service import (
    TEAMS,
    WherePlayError,
    WherePlayService,
    find_server,
    is_stale,
    normalize_join_code,
    safe_field_text,
    safe_number,
)

_SNAPSHOT = {
    "data": [
        {"serverId": "12345678", "name": "Alpha", "region": "eu", "map": {"variant": "Korsak"},
         "players": 80, "maxPlayers": 120},
        {"serverId": "0f8fad5b-d9cb-469f-a165-70867728950e", "name": "Bravo", "region": "ru",
         "map": {"variant": "Port"}, "players": 10, "maxPlayers": 100},
    ],
    "meta": {"stale": False, "fetchedAt": "2026-09-29T12:00:00Z", "refreshSeconds": 60},
}


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
async def db(tmp_path):
    database = Database(os.path.join(tmp_path, "test.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def bot():
    config = Config(
        token="dummy",
        prefix="!",
        db_path=":memory:",
        log_level="ERROR",
        status_activity="test",
        owner_id=None,
    )
    return SimpleNamespace(config=config, get_guild=lambda _guild_id: None)


@pytest.fixture
def repo(db):
    return WherePlayRepository(db)


@pytest.fixture
async def service(repo, bot):
    return WherePlayService(repo, bot)


def _stub_snapshot(service, snapshot):
    """Подменяет сетевой снапшот, чтобы тесты не ходили в WardogServers."""

    class _Stub:
        async def get(self, now=None):
            return snapshot

    service._snapshot = _Stub()


# --- выбор сервера при устаревшем снапшоте ---


async def test_select_server_succeeds_on_stale_snapshot_with_warning(service):
    await service.configure_channel(100, 200, 300)
    _stub_snapshot(service, {**_SNAPSHOT, "meta": {**_SNAPSHOT["meta"], "stale": True}})

    row, warning = await service.select_server(100, "12345678", TEAMS[0], 5)

    assert row is not None
    assert row["code"] == "12345678"
    assert warning is not None and "устаревшие" in warning
    assert "2026-09-29T12:00:00Z" in warning


async def test_select_server_has_no_warning_when_fresh(service):
    await service.configure_channel(100, 200, 300)
    fresh = {
        **_SNAPSHOT,
        "meta": {
            **_SNAPSHOT["meta"],
            "stale": False,
            "fetchedAt": _now().isoformat().replace("+00:00", "Z"),
        },
    }
    _stub_snapshot(service, fresh)

    row, warning = await service.select_server(100, "12345678", TEAMS[0], 5)

    assert row is not None
    assert warning is None


async def test_select_server_still_rejects_unknown_code(service):
    await service.configure_channel(100, 200, 300)
    _stub_snapshot(service, {**_SNAPSHOT, "meta": {**_SNAPSHOT["meta"], "stale": True}})

    with pytest.raises(WherePlayError, match="не найден"):
        await service.select_server(100, "does-not-exist", TEAMS[0], 5)


async def test_select_server_still_rejects_bad_team(service):
    await service.configure_channel(100, 200, 300)
    _stub_snapshot(service, _SNAPSHOT)

    with pytest.raises(WherePlayError, match="команду"):
        await service.select_server(100, "12345678", "фиолетовые", 5)



# --- код подключения ---


def test_normalize_accepts_numeric_and_uuid():
    assert normalize_join_code("  12345678 ") == "12345678"
    assert normalize_join_code("0F8FAD5B-D9CB-469F-A165-70867728950E") == "0f8fad5b-d9cb-469f-a165-70867728950e"


@pytest.mark.parametrize(
    "value",
    ["12345", "123-456", "a" * 40, "server_code_42", "a" * 128, "a" * 4, "A1_-"],
)
def test_normalize_accepts_codes_within_bounds(value):
    assert normalize_join_code(value) == value


@pytest.mark.parametrize(
    "value",
    ["Мой сервер", "12 34", "", "   ", "abc", "a" * 129, "сервер123", "код-123", "code/123", "code.123"],
)
def test_normalize_rejects_names_bad_length_and_odd_characters(value):
    with pytest.raises(WherePlayError):
        normalize_join_code(value)


def test_normalize_error_shows_input_and_expected_format():
    # Пользователь вводит название сервера: подсказка обязана показывать, что
    # он ввёл, и объяснять, где взять настоящий код.
    with pytest.raises(WherePlayError) as info:
        normalize_join_code("Мой сервер")
    message = str(info.value)
    assert "Мой сервер" in message
    assert "название сервера" in message
    assert "из игры" in message


def test_normalize_error_explains_wrong_length_and_symbols():
    with pytest.raises(WherePlayError) as short:
        normalize_join_code("abc")
    assert "длина 3 симв." in str(short.value)
    with pytest.raises(WherePlayError) as symbols:
        normalize_join_code("code/123")
    assert "недопустимые символы" in str(symbols.value)


def test_normalize_error_handles_empty_input():
    with pytest.raises(WherePlayError) as info:
        normalize_join_code("   ")
    assert "пусто" in str(info.value)


def test_normalize_honours_configured_bounds():
    # Границы из конфига важнее дефолтов: сервер может выдавать и короткие,
    # и длинные коды.
    with pytest.raises(WherePlayError):
        normalize_join_code("12345", minimum=6)
    with pytest.raises(WherePlayError):
        normalize_join_code("123456", maximum=5)
    assert normalize_join_code("123456", minimum=6, maximum=10) == "123456"


# --- снапшот ---


def test_find_server_matches_case_insensitively_and_detects_duplicates():
    assert find_server(_SNAPSHOT, "12345678")["name"] == "Alpha"
    assert find_server(_SNAPSHOT, "0F8FAD5B-D9CB-469F-A165-70867728950E")["name"] == "Bravo"
    assert find_server(_SNAPSHOT, "нет-такого") is None

    duplicated = {"data": _SNAPSHOT["data"] * 2, "meta": _SNAPSHOT["meta"]}
    with pytest.raises(WherePlayError):
        find_server(duplicated, "12345678")


def test_is_stale_checks_flag_timestamp_and_refresh():
    now = datetime(2026, 9, 29, 12, 1, tzinfo=UTC)
    assert is_stale(_SNAPSHOT, now) is False

    assert is_stale({**_SNAPSHOT, "meta": {**_SNAPSHOT["meta"], "stale": True}}, now) is True
    # Снапшот старше окна обновления + запас в минуту.
    assert is_stale(_SNAPSHOT, now + timedelta(minutes=5)) is True
    assert is_stale({"data": [], "meta": None}, now) is True
    assert is_stale({**_SNAPSHOT, "meta": {**_SNAPSHOT["meta"], "refreshSeconds": "много"}}, now) is True


def test_safe_field_text_escapes_markdown_and_mentions():
    assert safe_field_text("  обычный  ") == "обычный"
    assert safe_field_text("@everyone") == r"\@everyone"
    assert "\\*" in safe_field_text("**жирно**")
    assert safe_field_text(None) == "—"
    assert safe_field_text({"name": "Alpha"}) == "—"
    assert len(safe_field_text("x" * 500)) <= 200
    assert safe_number(True) == "—"
    assert safe_number(12) == "12"


# --- репозиторий ---


async def test_repository_channel_lifecycle(service, repo):
    assert await service.row(100) is None

    row = await service.configure_channel(100, 200, 300)
    assert row is not None
    assert int(row["channel_id"]) == 200
    assert int(row["role_id"]) == 300
    assert row["message_id"] is None
    assert int(row["active"]) == 0

    # Повторный setup обновляет канал и сбрасывает сообщение.
    await service.bind_message(100, 999)
    row = await service.configure_channel(100, 201, None)
    assert row is not None
    assert int(row["channel_id"]) == 201
    assert row["role_id"] is None
    assert row["message_id"] is None


async def test_repository_active_and_stop(service, repo):
    await service.configure_channel(100, 200, 300)
    payload = json.dumps({"server": _SNAPSHOT["data"][0], "fetchedAt": "2026-09-29T12:00:00Z"})

    row = await repo.get(100)
    assert row is not None
    await repo.set_active(
        100,
        code="12345678",
        team=TEAMS[0],
        caller_id=5,
        payload=payload,
        fetched_at=_now(),
    )
    active = await repo.active_guilds()
    assert [int(item["guild_id"]) for item in active] == [100]

    stopped = await service.stop(100)
    assert stopped is not None
    assert int(stopped["active"]) == 0
    assert stopped["code"] == "12345678"
    assert await repo.active_guilds() == []


async def test_caller_rooms_remember_and_forget(service, repo):
    await service.configure_channel(100, 200, None)
    await service.remember_caller_room(100, 11, 501)
    await service.remember_caller_room(100, 12, 502)
    # Повторное нажатие обновляет комнату, а не плодит записи.
    await service.remember_caller_room(100, 11, 503)

    rooms = await repo.caller_rooms(100)
    assert {int(room["member_id"]): int(room["channel_id"]) for room in rooms} == {11: 503, 12: 502}

    await service.forget_caller_room(100, 12)
    assert [int(room["member_id"]) for room in await repo.caller_rooms(100)] == [11]


# --- карточка ---


async def test_card_inactive_hides_server_data(service, repo):
    await service.configure_channel(100, 200, 300)
    row = await repo.get(100)
    assert row is not None
    embed = service.card(row, None, [])
    assert "общего сбора нет" in (embed.description or "")
    assert all("КОД" not in field.name for field in embed.fields)


async def test_card_active_renders_escaped_data(service, repo):
    await service.configure_channel(100, 200, 300)
    row = await repo.get(100)
    assert row is not None
    payload = {
        "server": {"name": "@everyone", "region": "eu", "map": {"variant": "Korsak"},
                   "players": 80, "maxPlayers": 120},
        "fetchedAt": "2026-09-29T12:00:00Z",
    }
    await repo.set_active(
        100, code="12345678", team=TEAMS[0], caller_id=5,
        payload=json.dumps(payload), fetched_at=_now(),
    )
    row = await repo.get(100)
    assert row is not None

    embed = service.card(row, None, ["<@11> — <#501>"])
    fields = {field.name: field.value for field in embed.fields}
    assert fields["СЕРВЕР"] == r"\@everyone"
    assert "12345678" in fields["КОД"]
    assert fields["ИГРОКИ"] == "80 / 120"
    assert fields["КАРТА"] == "Korsak"
    assert fields["КОЛЛЕРЫ"] == "<@11> — <#501>"
    assert "<@5>" in fields["СБОР ОБЪЯВИЛ"]
    assert "WardogServers" in (embed.footer.text or "")


async def test_card_marks_warning_and_missing_caller_rooms(service, repo):
    await service.configure_channel(100, 200, 300)
    row = await repo.get(100)
    assert row is not None
    await repo.set_active(
        100, code="12345678", team=TEAMS[1], caller_id=0,
        payload=json.dumps({"server": {}, "fetchedAt": None}), fetched_at=_now(),
    )
    row = await repo.get(100)
    assert row is not None

    embed = service.card(row, "⚠️ Источник передаёт устаревшие данные.", [])
    assert "устаревшие" in (embed.description or "")
    fields = {field.name: field.value for field in embed.fields}
    assert fields["НАША КОМАНДА"] == TEAMS[1]
    assert "Я коллер" in fields["КОЛЛЕРЫ"]
    # Без данных о сервере показываются прочерки, а не исключение.
    assert fields["СЕРВЕР"] == "—"
    assert fields["РЕГИОН"] == "—"
    assert "СБОР ОБЪЯВИЛ" not in fields


async def test_card_survives_broken_payload(service, repo):
    await service.configure_channel(100, 200, 300)
    row = await repo.get(100)
    assert row is not None
    await repo.set_active(
        100, code="12345678", team=TEAMS[0], caller_id=0,
        payload="{это не json", fetched_at=_now(),
    )
    row = await repo.get(100)
    assert row is not None
    fields = {field.name: field.value for field in service.card(row, None, []).fields}
    assert fields["СЕРВЕР"] == "—"

