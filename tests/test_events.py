"""Тесты порта ивентов: мастер, валидация, участие, напоминания."""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import discord
import pytest

from app.cogs.events.events import (
    EventTypeView,
    MentionRoleView,
    NotGoingView,
    PublishView,
)
from app.core.views import EVENT_LEGACY_OPTIONS, EVENT_SIGNUP_OPTIONS
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

    assert await service.set_signup(event, 11, "going") is True
    assert await service.set_signup(event, 12, "not_going") is True
    assert await service.set_signup(event, 13, "going") is True
    assert await service.set_signup(event, 13, "going") is False  # повтор снимает

    counts = await service.signup_counts(event_id)
    # Счётчики есть для всех ролей, чтобы карточка не «прыгала» при первом отклике.
    assert set(counts) == set(SIGNUP_ROLES)
    assert counts["going"] == [11]
    assert counts["not_going"] == [12]
    assert 13 not in (await service.participants(event_id))
    assert sorted(await service.participants(event_id)) == [11]

    with pytest.raises(EventValidationError):
        await service.set_signup(event, 11, "неизвестный")


async def test_signup_rejects_removed_legacy_roles(service):
    event_id = await _make_event(service)
    event = await service.get(event_id)
    for legacy in ("going_inf", "going_tech", "sl", "camera"):
        with pytest.raises(EventValidationError):
            await service.set_signup(event, 11, legacy)


async def test_signup_options_and_repo_roles_stay_in_sync():
    assert set(SIGNUP_ROLES) == set(EVENT_SIGNUP_OPTIONS) == {"going", "maybe", "not_going"}
    assert not set(SIGNUP_ROLES) & set(EVENT_LEGACY_OPTIONS)


async def test_legacy_marks_are_still_counted_and_shown(db, service):
    event_id = await _make_event(service)
    # Снятые варианты репозиторий больше не принимает, поэтому легаси пишем
    # прямо в SQL — так выглядит база, созданная до перехода на кнопки.
    for user_id, role in ((21, "going_inf"), (22, "going_tech"), (23, "camera"), (24, "not_going")):
        await db.execute(
            "INSERT INTO event_signup (event_id, user_id, role, updated_at) VALUES (?, ?, ?, ?)",
            (event_id, user_id, role, datetime.now(UTC).isoformat()),
        )

    counts = await service.signup_counts(event_id)
    assert counts["going_inf"] == [21]
    assert counts["going"] == []

    embed = await service.embed(await service.get(event_id))
    rendered = "\n".join(f.name for f in embed.fields)
    assert "Прежние отметки (3)" in rendered
    # Прежние «иду»-отметки остаются участниками: они положительные,
    # значит должны попадать в напоминания. А «Не иду» — нет.
    assert sorted(await service.participants(event_id)) == [21, 22, 23]


async def test_maybe_is_selectable_again(service):
    event_id = await _make_event(service)
    event = await service.get(event_id)
    assert await service.set_signup(event, 31, "maybe") is True
    counts = await service.signup_counts(event_id)
    assert counts["maybe"] == [31]
    # «Возможно» не отказ: участником сбор считается.
    assert await service.participants(event_id) == [31]


async def test_signup_blocked_after_cancel(service):
    event_id = await _make_event(service)
    await service.cancel(event_id)
    cancelled = await service.get(event_id)
    assert cancelled is not None and cancelled["active"] == 0
    with pytest.raises(EventValidationError):
        await service.set_signup(cancelled, 11, "going")


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


# --- кнопки мастера: Discord ждёт ответ ровно 3 секунды ---


class _FakeResponse:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls

    async def send_message(self, **kwargs) -> None:
        self._calls.append(("send", kwargs))

    async def edit_message(self, **kwargs) -> None:
        self._calls.append(("edit", kwargs))

    async def defer(self, **kwargs) -> None:
        self._calls.append(("defer", kwargs))


class _FakeInteraction:
    """Заглушка interaction: важно лишь, что на нажатие ушёл хоть один ответ."""

    def __init__(self, user_id: int) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = 100
        self.calls: list[tuple[str, dict]] = []
        self.response = _FakeResponse(self.calls)
        self.data = {"custom_id": "event_pick:test"}

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class _FakeRole:
    """Заглушка discord.Role.

    ``mentionable`` повторяет слот- поле настоящего discord.Role, а не
    выдуманный метод: на выдуманном API тесты проходили, а бот падал.
    """

    __slots__ = ("id", "name", "position", "mention", "mentionable")

    def __init__(self, role_id: int, name: str, position: int, mentionable: bool = True) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.mention = f"<@&{role_id}>"
        self.mentionable = mentionable


class _FakeGuild:
    def __init__(self, roles: list[_FakeRole]) -> None:
        self.roles = roles
        self.id = 999
        self.mentionable = roles

    def get_role(self, role_id: int):
        return next((role for role in self.roles if role.id == role_id), None)


def _fake_guild(role_count: int = 3) -> _FakeGuild:
    roles = [_FakeRole(1000 + i, f"Роль {i}", i) for i in range(role_count)]
    roles.append(_FakeRole(999, "@everyone", -1, mentionable=False))
    return _FakeGuild(roles)


async def test_type_button_answers_and_advances_step(service):
    flow = service.flows.begin(EventFlow(user_id=7, guild_id=100, channel_id=200, step="type"))
    view = EventTypeView(7, service.flows.token_for(flow), service)
    interaction = _FakeInteraction(7)

    await view.children[0].callback(interaction)

    # Раньше ответ не уходил вовсе — Discord показывал «Приложение не ответило вовремя».
    assert interaction.names == ["edit"]
    assert flow.step == "description"


async def test_not_going_button_hands_over_to_mention_step(service):
    flow = service.flows.begin(EventFlow(user_id=7, guild_id=100, channel_id=200, step="not_going"))
    view = NotGoingView(7, service.flows.token_for(flow), service, _fake_guild())
    interaction = _FakeInteraction(7)

    await view.children[0].callback(interaction)

    assert interaction.names == ["edit"]
    assert flow.step == "mention"
    # Шаг не должен упираться в тупик: дальше идёт выбор роли для упоминания.
    next_view = interaction.calls[0][1]["view"]
    assert isinstance(next_view, MentionRoleView)


async def test_mention_role_select_stores_choice_and_hands_to_publish(service):
    flow = service.flows.begin(EventFlow(user_id=7, guild_id=100, channel_id=200, step="mention"))
    guild = _fake_guild()
    view = MentionRoleView(7, service.flows.token_for(flow), service, guild)
    interaction = _FakeInteraction(7)

    select = view.children[0]
    assert isinstance(select, discord.ui.Select)
    select._values = [str(guild.mentionable[0].id)]
    await select.callback(interaction)

    assert interaction.names == ["edit"]
    assert flow.step == "publish"
    assert flow.data["mention_role_id"] == guild.mentionable[0].id
    assert isinstance(interaction.calls[0][1]["view"], PublishView)


async def test_mention_role_none_disables_ping(service):
    flow = service.flows.begin(EventFlow(user_id=7, guild_id=100, channel_id=200, step="mention"))
    guild = _fake_guild()
    view = MentionRoleView(7, service.flows.token_for(flow), service, guild)
    interaction = _FakeInteraction(7)

    select = view.children[0]
    assert isinstance(select, discord.ui.Select)
    select._values = ["none"]
    await select.callback(interaction)

    assert flow.data["mention_role_id"] is None
    assert isinstance(interaction.calls[0][1]["view"], PublishView)


async def test_mention_role_list_caps_at_discord_limit(service):
    flow = service.flows.begin(EventFlow(user_id=7, guild_id=100, channel_id=200, step="mention"))
    guild = _fake_guild(role_count=40)
    view = MentionRoleView(7, service.flows.token_for(flow), service, guild)

    select = view.children[0]
    assert isinstance(select, discord.ui.Select)
    # Discord разрешает не больше 25 пунктов: 24 роли плюс «Без упоминания».
    assert len(select.options) == 25
    assert select.options[-1].value == "none"


async def test_mention_role_skips_roles_that_cannot_be_pinged(service):
    flow = service.flows.begin(EventFlow(user_id=7, guild_id=100, channel_id=200, step="mention"))
    guild = _fake_guild(role_count=2)
    # Роль без права упоминания и @everyone в список попадать не должны.
    guild.roles.append(_FakeRole(1500, "Тихая роль", 5, mentionable=False))
    view = MentionRoleView(7, service.flows.token_for(flow), service, guild)

    select = view.children[0]
    assert isinstance(select, discord.ui.Select)
    values = {option.value for option in select.options}
    assert values == {"1000", "1001", "none"}
    assert "Тихая роль" not in {option.label for option in select.options}
    assert "@everyone" not in {option.label for option in select.options}


def test_roles_are_filtered_with_real_discord_api():
    """Сторож от выдуманного API: у discord.Role есть слот mentionable.

    На заглушке с выдуманным методом is_mentionable() тесты проходили,
    а бот падал в бою с AttributeError.
    """
    assert hasattr(discord.Role, "mentionable")
    assert not hasattr(discord.Role, "is_mentionable")
    assert _FakeRole(1, "Роль", 0).mentionable is True


async def test_step_failure_answers_instead_of_going_silent(service, caplog):
    """Сбой внутри кнопки раньше выглядел как «Приложение не ответило вовремя»."""
    flow = service.flows.begin(EventFlow(user_id=7, guild_id=100, channel_id=200, step="not_going"))
    view = NotGoingView(7, service.flows.token_for(flow), service, _fake_guild())
    interaction = _FakeInteraction(7)

    def boom(_flow):
        raise RuntimeError("сломалось")

    view._next_step = boom  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        await view.children[0].callback(interaction)

    # Ответ ушёл, мастер закрыт, причина есть в логе.
    assert interaction.names == ["send"]
    assert service.flows.get(7) is None
    assert "Шаг мастера ивента упал" in caplog.text
    assert "сломалось" in caplog.text


async def test_expired_flow_answers_instead_of_staying_silent(service):
    view = EventTypeView(7, 987654, service)
    interaction = _FakeInteraction(7)

    await view.children[0].callback(interaction)

    assert interaction.names == ["edit"]
