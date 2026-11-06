"""Система отпусков: разбор дат, правила периода, текст информации, сервис поверх БД."""

from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.cogs.vacations.vacations import (
    VACATION_CANCEL_ID,
    VACATION_EXTEND_ID,
    VACATION_REQUEST_ID,
    VACATION_STATUS_ID,
    VacationPanelView,
)
from app.db.database import Database
from app.db.vacations_repository import VacationsRepository
from app.services.vacation_service import (
    INFO_DESCRIPTION_LIMIT,
    INFO_EMPTY_TEXT,
    INFO_TITLE,
    MAX_REASON_LENGTH,
    PANEL_DESCRIPTION,
    PANEL_TITLE,
    VacationError,
    VacationService,
    build_info_text,
    format_date,
    format_period,
    info_embed,
    panel_embed,
    parse_date,
    pick_target,
    validate_period,
    validate_reason,
)

TODAY = date(2026, 8, 1)
GUILD = 1
USER = 11


def _row(vacation_id: int, user_id: int, start: str, end: str, reason: str = "Причина") -> dict:
    return {
        "id": vacation_id,
        "guild_id": GUILD,
        "user_id": user_id,
        "start_date": start,
        "end_date": end,
        "reason": reason,
        "created_at": "2026-07-01T00:00:00+00:00",
        "status": "active",
        "updated_at": None,
    }


def _discord_error(exc_type: type[discord.HTTPException]) -> discord.HTTPException:
    return exc_type(MagicMock(status=404, reason="Not Found"), "Discord error")


# --- разбор дат ---


def test_parse_date_accepts_russian_format() -> None:
    assert parse_date("05.08.2026") == date(2026, 8, 5)
    assert parse_date("  5.8.2026 ") == date(2026, 8, 5)


@pytest.mark.parametrize("text", ["2026-08-05", "05/08/2026", "", "ab.cd.efgh", "31.02.2026", "00.08.2026"])
def test_parse_date_rejects_invalid_input(text: str) -> None:
    with pytest.raises(VacationError):
        parse_date(text)


def test_format_date_accepts_date_and_iso_string() -> None:
    assert format_date(date(2026, 8, 5)) == "05.08.2026"
    assert format_date("2026-08-05") == "05.08.2026"
    assert format_period("2026-08-05", "2026-08-23") == "от 05.08.2026 до 23.08.2026"


# --- правила периода и причины ---


def test_validate_period_accepts_regular_vacation() -> None:
    validate_period(date(2026, 8, 5), date(2026, 8, 23), TODAY)


def test_validate_period_accepts_start_today() -> None:
    validate_period(TODAY, TODAY, TODAY)


def test_validate_period_has_no_maximum_length() -> None:
    validate_period(TODAY, TODAY + timedelta(days=730), TODAY)


def test_validate_period_rejects_start_in_past() -> None:
    with pytest.raises(VacationError, match="в прошлом"):
        validate_period(TODAY - timedelta(days=1), TODAY + timedelta(days=5), TODAY)


def test_validate_period_rejects_end_before_start() -> None:
    with pytest.raises(VacationError, match="раньше даты начала"):
        validate_period(date(2026, 8, 10), date(2026, 8, 5), TODAY)


def test_validate_reason_strips_and_limits() -> None:
    assert validate_reason("  семья  ") == "семья"
    with pytest.raises(VacationError):
        validate_reason("   ")
    with pytest.raises(VacationError):
        validate_reason("x" * (MAX_REASON_LENGTH + 1))
    assert validate_reason("x" * MAX_REASON_LENGTH) == "x" * MAX_REASON_LENGTH


def test_pick_target_prefers_current_then_nearest_upcoming() -> None:
    rows = [_row(3, USER, "2026-10-01", "2026-10-05"), _row(2, USER, "2026-08-20", "2026-08-25")]
    assert pick_target(rows, TODAY)["id"] == 2
    current = [_row(1, USER, "2026-07-28", "2026-08-03"), _row(2, USER, "2026-08-20", "2026-08-25")]
    assert pick_target(current, TODAY)["id"] == 1
    assert pick_target([_row(1, USER, "2026-07-01", "2026-07-10")], TODAY) is None


# --- текст информационного сообщения ---


def test_info_text_empty_message() -> None:
    assert build_info_text([], TODAY) == INFO_EMPTY_TEXT == "Сейчас никто не в отпуске."


def test_info_text_sorted_by_start_and_formatted() -> None:
    rows = [
        _row(2, 22, "2026-09-01", "2026-09-05", "Семья"),
        _row(1, 11, "2026-08-05", "2026-08-23", "Учёба"),
    ]
    assert build_info_text(rows, TODAY) == ("<@11>\nот 05.08.2026 до 23.08.2026\nУчёба\n\n<@22>\nот 01.09.2026 до 05.09.2026\nСемья")


def test_info_text_skips_ended_vacations() -> None:
    rows = [_row(1, USER, "2026-07-01", "2026-07-31", "Прошлый"), _row(2, USER, "2026-08-01", "2026-08-02", "Текущий")]
    text = build_info_text(rows, TODAY)
    assert "Прошлый" not in text
    assert "Текущий" in text


def test_info_text_truncates_within_embed_limit() -> None:
    rows = [_row(i, i, "2026-08-05", "2026-08-06", "x" * 300) for i in range(1, 60)]
    text = build_info_text(rows, TODAY)
    assert len(text) <= INFO_DESCRIPTION_LIMIT
    assert text.endswith(tuple(f"и ещё {n}" for n in range(1, 60)))


def test_info_embed_and_panel_embed_match_reference() -> None:
    embed = info_embed("текст")
    assert embed.title == INFO_TITLE == "Система отпусков | Информация"
    assert embed.color == discord.Color(0xF0932B)
    assert embed.footer.text is None
    assert embed.description == "текст"

    panel = panel_embed()
    assert panel.title == PANEL_TITLE == "Система отпусков | Панель"
    assert panel.color == discord.Color.blurple()
    assert panel.description == PANEL_DESCRIPTION
    assert "🔷 **Снять отпуск** — отменить текущий отпуск" in PANEL_DESCRIPTION


async def test_panel_view_is_persistent_with_fixed_ids() -> None:
    view = VacationPanelView(None)  # type: ignore[arg-type]
    assert view.timeout is None
    buttons = {item.custom_id: item for item in view.children}
    assert set(buttons) == {VACATION_REQUEST_ID, VACATION_STATUS_ID, VACATION_EXTEND_ID, VACATION_CANCEL_ID}
    assert buttons[VACATION_REQUEST_ID].label == "Взять отпуск"
    assert buttons[VACATION_REQUEST_ID].style is discord.ButtonStyle.success
    assert buttons[VACATION_STATUS_ID].style is discord.ButtonStyle.primary
    assert buttons[VACATION_EXTEND_ID].label == "Продлить отпуск"
    assert buttons[VACATION_CANCEL_ID].style is discord.ButtonStyle.danger


# --- сервис поверх БД ---


@pytest.fixture
async def database(tmp_path):
    db = Database(os.path.join(tmp_path, "vacations.db"))
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def service(database):
    return VacationService(VacationsRepository(database))


async def test_create_and_list_for_user(service: VacationService) -> None:
    created = await service.create(GUILD, USER, "05.08.2026", "23.08.2026", "Семья", today=TODAY)
    assert created["start_date"] == "2026-08-05"
    assert created["status"] == "active"
    rows = await service.list_for_user(GUILD, USER, today=TODAY)
    assert [row["id"] for row in rows] == [created["id"]]
    assert await service.list_for_user(GUILD, 999, today=TODAY) == []


async def test_create_rejects_start_in_past(service: VacationService) -> None:
    with pytest.raises(VacationError, match="в прошлом"):
        await service.create(GUILD, USER, "31.07.2026", "05.08.2026", "x", today=TODAY)
    assert await service.list_for_user(GUILD, USER, today=TODAY) == []


async def test_create_accepts_very_long_vacation(service: VacationService) -> None:
    created = await service.create(GUILD, USER, "01.08.2026", "01.08.2028", "долгий", today=TODAY)
    assert created["end_date"] == "2028-08-01"


async def test_create_rejects_second_vacation_for_same_user(service: VacationService) -> None:
    await service.create(GUILD, USER, "05.08.2026", "23.08.2026", "Первый", today=TODAY)
    with pytest.raises(VacationError) as info:
        await service.create(GUILD, USER, "01.09.2026", "05.09.2026", "Второй", today=TODAY)
    message = str(info.value)
    assert "Продлить отпуск" in message
    assert "Снять отпуск" in message
    rows = await service.list_for_user(GUILD, USER, today=TODAY)
    assert [row["reason"] for row in rows] == ["Первый"]


async def test_other_users_may_overlap(service: VacationService) -> None:
    await service.create(GUILD, USER, "05.08.2026", "23.08.2026", "Первый", today=TODAY)
    await service.create(GUILD, USER + 1, "10.08.2026", "20.08.2026", "Другой", today=TODAY)
    text = await service.info_description(GUILD, today=TODAY)
    assert "Первый" in text and "Другой" in text


async def test_new_vacation_allowed_after_cancel(service: VacationService) -> None:
    await service.create(GUILD, USER, "05.08.2026", "23.08.2026", "Первый", today=TODAY)
    await service.cancel(GUILD, USER, today=TODAY)
    created = await service.create(GUILD, USER, "01.09.2026", "05.09.2026", "Новый", today=TODAY)
    assert created["reason"] == "Новый"


async def test_new_vacation_allowed_after_auto_end(service: VacationService) -> None:
    await service.create(GUILD, USER, "01.08.2026", "05.08.2026", "Прошлый", today=TODAY)
    later = date(2026, 8, 10)
    assert await service.mark_ended(today=later) == 1
    created = await service.create(GUILD, USER, "11.08.2026", "15.08.2026", "Новый", today=later)
    assert created["reason"] == "Новый"


async def test_create_runs_period_and_reason_validation(service: VacationService) -> None:
    with pytest.raises(VacationError):
        await service.create(GUILD, USER, "10.08.2026", "05.08.2026", "x", today=TODAY)
    with pytest.raises(VacationError):
        await service.create(GUILD, USER, "01.08.2026", "05.08.2026", "   ", today=TODAY)


async def test_extend_moves_end_forward(service: VacationService) -> None:
    await service.create(GUILD, USER, "05.08.2026", "10.08.2026", "x", today=TODAY)
    extended = await service.extend(GUILD, USER, "15.08.2026", today=TODAY)
    assert extended["end_date"] == "2026-08-15"
    assert extended["updated_at"] is not None
    rows = await service.list_for_user(GUILD, USER, today=TODAY)
    assert rows[0]["end_date"] == "2026-08-15"


async def test_extend_ongoing_vacation_whose_start_has_passed(service: VacationService) -> None:
    await service.create(GUILD, USER, "01.08.2026", "05.08.2026", "идёт", today=TODAY)
    extended = await service.extend(GUILD, USER, "12.08.2026", today=date(2026, 8, 3))
    assert extended["end_date"] == "2026-08-12"


async def test_extend_requires_later_end(service: VacationService) -> None:
    await service.create(GUILD, USER, "05.08.2026", "10.08.2026", "x", today=TODAY)
    with pytest.raises(VacationError, match="позже текущей"):
        await service.extend(GUILD, USER, "10.08.2026", today=TODAY)


async def test_extend_without_vacation_fails(service: VacationService) -> None:
    with pytest.raises(VacationError, match="продлить"):
        await service.extend(GUILD, USER, "15.08.2026", today=TODAY)


async def test_cancel_keeps_row_with_cancelled_status(service: VacationService, database: Database) -> None:
    created = await service.create(GUILD, USER, "01.08.2026", "05.08.2026", "текущий", today=TODAY)
    cancelled = await service.cancel(GUILD, USER, today=TODAY)
    assert cancelled["reason"] == "текущий"
    assert await service.list_for_user(GUILD, USER, today=TODAY) == []

    stored = await service.repo.get(created["id"])
    assert stored is not None
    assert stored["status"] == "cancelled"
    assert stored["updated_at"] is not None

    with pytest.raises(VacationError):
        await service.cancel(GUILD, USER, today=TODAY)


async def test_mark_ended_sets_status_and_hides_from_list(service: VacationService) -> None:
    repo = service.repo
    past_id = await repo.insert(GUILD, USER, "2026-07-01", "2026-07-31", "прошлый", "2026-06-01T00:00:00+00:00")
    await service.create(GUILD, USER + 1, "01.08.2026", "05.08.2026", "текущий", today=TODAY)

    assert await service.mark_ended(today=TODAY) == 1
    stored = await repo.get(past_id)
    assert stored is not None
    assert stored["status"] == "ended"
    assert stored["updated_at"] is not None
    assert await service.mark_ended(today=TODAY) == 0

    assert [row["reason"] for row in await repo.for_guild(GUILD)] == ["текущий"]
    assert "прошлый" not in await service.info_description(GUILD, today=TODAY)
    assert await service.list_for_user(GUILD, USER, today=TODAY) == []


async def test_info_description_reflects_database(service: VacationService) -> None:
    assert await service.info_description(GUILD, today=TODAY) == INFO_EMPTY_TEXT
    await service.create(GUILD, USER, "05.08.2026", "23.08.2026", "Учёба", today=TODAY)
    text = await service.info_description(GUILD, today=TODAY)
    assert text == f"<@{USER}>\nот 05.08.2026 до 23.08.2026\nУчёба"


async def test_setup_settings_roundtrip(service: VacationService) -> None:
    assert await service.settings(GUILD) is None
    await service.save_setup(GUILD, 500, 600, 700)
    settings = await service.settings(GUILD)
    assert settings is not None
    assert (settings["channel_id"], settings["info_message_id"], settings["panel_message_id"]) == (500, 600, 700)
    await service.repo.clear_messages(GUILD)
    settings = await service.settings(GUILD)
    assert settings is not None
    assert settings["info_message_id"] is None and settings["panel_message_id"] is None


def _bound_service(database: Database, channel: MagicMock, log: MagicMock | None = None) -> VacationService:
    bot = MagicMock()
    bot.get_channel.return_value = channel
    bot.get_guild.return_value = MagicMock(name="guild")
    return VacationService(VacationsRepository(database), bot, log)


def _text_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 500
    channel.send = AsyncMock(return_value=MagicMock(id=0))
    channel.partial = MagicMock()
    channel.get_partial_message.return_value = channel.partial
    channel.partial.edit = AsyncMock()
    channel.partial.fetch = AsyncMock(return_value=MagicMock())
    return channel


async def test_refresh_info_reposts_when_message_deleted(database: Database) -> None:
    channel = _text_channel()
    channel.partial.edit = AsyncMock(side_effect=_discord_error(discord.NotFound))
    channel.send = AsyncMock(return_value=MagicMock(id=601))
    service = _bound_service(database, channel)
    await service.save_setup(GUILD, 500, 600, 700)

    await service.refresh_info(GUILD)  # не должен бросать исключение

    channel.partial.edit.assert_awaited_once()
    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["embed"].title == INFO_TITLE
    settings = await service.settings(GUILD)
    assert settings is not None and settings["info_message_id"] == 601
    assert settings["panel_message_id"] == 700


async def test_refresh_info_without_stored_message_does_nothing(database: Database) -> None:
    channel = _text_channel()
    service = _bound_service(database, channel)
    await service.refresh_info(GUILD)
    channel.send.assert_not_awaited()


async def test_check_panel_reposts_when_message_deleted(database: Database) -> None:
    channel = _text_channel()
    channel.partial.fetch = AsyncMock(side_effect=_discord_error(discord.NotFound))
    channel.send = AsyncMock(return_value=MagicMock(id=701))
    service = _bound_service(database, channel)
    await service.save_setup(GUILD, 500, 600, 700)
    view = MagicMock(spec=discord.ui.View)

    await service.check_panel(GUILD, lambda: view)

    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["view"] is view
    assert channel.send.await_args.kwargs["embed"].title == PANEL_TITLE
    settings = await service.settings(GUILD)
    assert settings is not None and settings["panel_message_id"] == 701
    assert settings["info_message_id"] == 600


async def test_check_panel_keeps_existing_message(database: Database) -> None:
    channel = _text_channel()
    service = _bound_service(database, channel)
    await service.save_setup(GUILD, 500, 600, 700)
    await service.check_panel(GUILD, lambda: MagicMock())
    channel.send.assert_not_awaited()


async def test_missing_channel_clears_links_without_crashing(database: Database) -> None:
    bot = MagicMock()
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=_discord_error(discord.NotFound))
    service = VacationService(VacationsRepository(database), bot)
    await service.save_setup(GUILD, 500, 600, 700)

    await service.refresh_all(lambda: MagicMock())

    settings = await service.settings(GUILD)
    assert settings is not None
    assert settings["info_message_id"] is None and settings["panel_message_id"] is None


async def test_failed_repost_clears_links_without_crashing(database: Database) -> None:
    channel = _text_channel()
    channel.partial.edit = AsyncMock(side_effect=_discord_error(discord.NotFound))
    channel.send = AsyncMock(side_effect=_discord_error(discord.Forbidden))
    service = _bound_service(database, channel)
    await service.save_setup(GUILD, 500, 600, 700)

    await service.refresh_info(GUILD)

    settings = await service.settings(GUILD)
    assert settings is not None
    assert settings["info_message_id"] is None and settings["panel_message_id"] is None


async def test_actions_are_logged(database: Database) -> None:
    log = MagicMock()
    log.log_event = AsyncMock()
    service = _bound_service(database, _text_channel(), log)

    await service.create(GUILD, USER, "05.08.2026", "10.08.2026", "Семья", today=TODAY)
    await service.extend(GUILD, USER, "12.08.2026", today=TODAY)
    await service.cancel(GUILD, USER, today=TODAY)
    await service.create(GUILD, USER, "01.09.2026", "02.09.2026", "Другой", today=TODAY)
    await service.repo.insert(GUILD, USER + 1, "2026-07-01", "2026-07-02", "прошлый", "2026-06-01T00:00:00+00:00")
    assert await service.mark_ended(today=TODAY) == 1

    titles = [call.args[1] for call in log.log_event.await_args_list]
    assert titles == [
        "Отпуск оформлен",
        "Отпуск продлён",
        "Отпуск снят",
        "Отпуск оформлен",
        "Отпуск завершён автоматически",
    ]


async def test_logging_failure_does_not_break_request(database: Database) -> None:
    log = MagicMock()
    log.log_event = AsyncMock(side_effect=RuntimeError("журнал недоступен"))
    service = _bound_service(database, _text_channel(), log)

    created = await service.create(GUILD, USER, "05.08.2026", "10.08.2026", "Семья", today=TODAY)
    assert created["reason"] == "Семья"
    assert await service.list_for_user(GUILD, USER, today=TODAY) != []
