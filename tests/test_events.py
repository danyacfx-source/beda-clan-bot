"""Тесты порта ивентов: мастер, валидация, участие, напоминания."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import Database
from app.db.events_repository import SIGNUP_ROLES, EventsRepository
from app.services.event_service import (
    EVENT_TYPES,
    EventFlow,
    EventFlowStore,
    EventService,
    EventValidationError,
    clean_image_url,
    clean_name,
    format_event_datetime,
    parse_event_datetime,
)


@pytest.fixture
async def db(tmp_path):
    database = Database(os.path.join(tmp_path, "test.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def service(db):
    return EventService(EventsRepository(db), reminder_lead_minutes=15)


async def _make_event(service: EventService, **overrides) -> int:
    now = datetime.now(UTC)
    payload = {
        "guild_id": 100,
        "channel_id": 200,
        "creator_id": 7,
        "name": "Тренировка",
        "event_type": "competitive",
        "description": "Собираемся в голосе",
        "briefing_at": now + timedelta(hours=2),
        "start_at": now + timedelta(hours=3),
        "image_url": "",
        "show_not_going": True,
    }
    payload.update(overrides)
    return await service.create(**payload)


# --- даты и текст ---


def test_parse_datetime_treats_input_as_moscow_time():
    parsed = parse_event_datetime("25.04.2026 19:00")
    assert parsed == datetime(2026, 4, 25, 16, 0, tzinfo=UTC)
    assert format_event_datetime(parsed) == "25.04.2026 19:00"


def test_parse_datetime_accepts_iso_and_rejects_garbage():
    assert parse_event_datetime("2026-04-25T19:00:00+03:00") == datetime(2026, 4, 25, 16, 0, tzinfo=UTC)
    with pytest.raises(EventValidationError):
        parse_event_datetime("25.04.2026")
    with pytest.raises(EventValidationError):
        parse_event_datetime("31.02.2026 19:00")


def test_clean_helpers_reject_empty_and_non_http():
    assert clean_name("  Сбор  ") == "Сбор"
    with pytest.raises(EventValidationError):
        clean_name("   ")
    assert clean_image_url("-") == ""
    with pytest.raises(EventValidationError):
        clean_image_url("javascript:alert(1)")


# --- создание ---


async def test_create_rejects_bad_schedule_and_unknown_type(service):
    now = datetime.now(UTC)
    with pytest.raises(EventValidationError):
        await _make_event(service, start_at=now + timedelta(hours=1), briefing_at=now + timedelta(hours=2))
    with pytest.raises(EventValidationError):
        await _make_event(service, briefing_at=now - timedelta(hours=3), start_at=now + timedelta(hours=2))
    with pytest.raises(EventValidationError):
        await _make_event(service, event_type="нет такого")


async def test_create_persists_and_binds_message(service):
    event_id = await _make_event(service)
    event = await service.get(event_id)
    assert event is not None
    assert event["name"] == "Тренировка"
    assert event["active"] == 1
    assert event["message_id"] is None
    assert int(event["creator_id"]) == 7

    await service.bind_message(event_id, 555)
    found = await service.get_by_message(555)
    assert found is not None and int(found["id"]) == event_id


async def test_recent_for_guild_ignores_other_guilds(service):
    await _make_event(service)
    await _make_event(service, guild_id=999)
    rows = await service.recent_for_guild(100, limit=10)
    assert len(rows) == 1
    assert all(int(row["guild_id"]) == 100 for row in rows)


# --- участие ---


async def test_signup_sets_toggles_and_splits_going(service):
    event_id = await _make_event(service)
    event = await service.get(event_id)

    assert await service.set_signup(event, 11, "going_inf") is True
    assert await service.set_signup(event, 12, "going_tech") is True
    assert await service.set_signup(event, 13, "not_going") is True
    assert await service.set_signup(event, 11, "going_inf") is False  # повтор снимает

    counts = await service.signup_counts(event_id)
    # Счётчики есть для всех ролей, чтобы карточка не «прыгала» при первом отклике.
    assert set(counts) == set(SIGNUP_ROLES)
    assert counts["going_inf"] == []
    assert counts["going_tech"] == [12]
    assert counts["not_going"] == [13]
    assert 11 not in (await service.participants(event_id))
    assert sorted(await service.participants(event_id)) == [12]

    with pytest.raises(EventValidationError):
        await service.set_signup(event, 11, "неизвестный")


async def test_signup_blocked_after_cancel(service):
    event_id = await _make_event(service)
    await service.cancel(event_id)
    cancelled = await service.get(event_id)
    assert cancelled is not None and cancelled["active"] == 0
    with pytest.raises(EventValidationError):
        await service.set_signup(cancelled, 11, "maybe")


# --- редактирование ---


async def test_update_validates_fields_and_schedule(service):
    event_id = await _make_event(service)
    event = await service.get(event_id)

    await service.update(event, "name", "  Новый сбор ")
    updated = await service.get(event_id)
    assert updated is not None and updated["name"] == "Новый сбор"

    with pytest.raises(EventValidationError):
        await service.update(event, "creator_id", "hack")
    with pytest.raises(EventValidationError):
        await service.update(event, "image_url", "ftp://x")

    # Начало раньше сбора — расписание неверно.
    future = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(EventValidationError):
        await service.update(event, "briefing_at", format_event_datetime(future + timedelta(hours=1)))


async def test_update_rejects_cancelled_event(service):
    event_id = await _make_event(service)
    await service.cancel(event_id)
    event = await service.get(event_id)
    with pytest.raises(EventValidationError):
        await service.update(event, "name", "Слишком поздно")


# --- напоминания ---


async def test_due_reminders_respect_window_and_flags(service):
    now = datetime.now(UTC)
    await _make_event(service, briefing_at=now + timedelta(minutes=5), start_at=now + timedelta(minutes=30))
    due = await service.due_reminders(now)
    assert len(due) == 1

    # Сбор попадает в окно, начало — нет.
    event = due[0]
    pending = service.pending_reminders(event, now)
    assert [kind for kind, _, _ in pending] == ["briefing"]

    await service.mark_reminded(int(event["id"]), briefing=True)
    event = await service.get(int(event["id"]))
    assert event is not None
    assert service.pending_reminders(event, now) == []

    # Дальше в окно попадает начало.
    later = now + timedelta(minutes=20)
    event = await service.get(int(event["id"]))
    assert event is not None
    kinds = [kind for kind, _, _ in service.pending_reminders(event, later)]
    assert kinds == ["start"]


async def test_reminders_skip_cancelled_and_distant_events(service):
    now = datetime.now(UTC)
    await _make_event(service, briefing_at=now + timedelta(hours=5), start_at=now + timedelta(hours=6))
    assert await service.due_reminders(now) == []

    event_id = await _make_event(service, briefing_at=now + timedelta(minutes=5), start_at=now + timedelta(minutes=9))
    await service.cancel(event_id)
    assert await service.due_reminders(now) == []


# --- мастер ---


def test_flow_store_keeps_one_flow_per_user():
    store = EventFlowStore()
    flow = store.begin(EventFlow(user_id=1, guild_id=2, channel_id=3, step="name"))
    assert store.get(1) is flow

    token = store.token_for(flow)
    assert isinstance(token, int)
    assert store.get_by_token(1, token) is flow
    # Чужой пользователь не видит мастер по токену.
    assert store.get_by_token(999, token) is None
    # Каждый вызов выдаёт новый токен на тот же поток.
    assert store.token_for(flow) != token

    # Второй мастер вытесняет первый.
    second = store.begin(EventFlow(user_id=1, guild_id=2, channel_id=3, step="name"))
    assert store.get(1) is second
    assert store.get_by_token(1, token) is None

    store.discard(1)
    assert store.get(1) is None


def test_flow_store_drops_expired_flows():
    store = EventFlowStore()
    flow = store.begin(EventFlow(user_id=1, guild_id=2, channel_id=3, step="name"))
    token = store.token_for(flow)
    assert store.get_by_token(1, token) is not None
    flow.touched_at = flow.touched_at - timedelta(hours=3)
    assert store.get(1) is None
    assert store.get_by_token(1, token) is None


def test_flow_expiry_uses_ttl():
    flow = EventFlow(user_id=1, guild_id=2, channel_id=3, step="name")
    assert flow.expired() is False
    assert flow.expired(flow.touched_at + timedelta(hours=3)) is True


def test_event_types_have_labels():
    for key, (label, emoji, color) in EVENT_TYPES.items():
        assert key and label and emoji
