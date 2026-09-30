"""Регрессионные тесты интерактивных представлений."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord

from app.core.loader import _migrate_event_cards
from app.core.views import EVENT_SIGNUP_OPTIONS, ConfirmView, EventSignupView, _handle_event_signup


class _FakeResponse:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def send_message(self, *, embed=None, ephemeral: bool = False) -> None:
        self._calls.append(getattr(embed, "title", "") or "")


class _FakeInteraction:
    """Подделка interaction.

    ``data`` — обычный dict, ровно как discord.py кладёт в
    ``Interaction.data`` распарсенный JSON. Если подделка сделает из него
    объект, тест перестанет ловить баг с getattr по dict.
    """

    def __init__(self, data: dict | None, calls: list[str]) -> None:
        self.data = data
        self.calls = calls
        self.guild_id = 100
        self.user = SimpleNamespace(id=555)
        self.client = SimpleNamespace(services=object())
        self.message = None
        self.response = _FakeResponse(calls)


class _FakeEvents:
    def __init__(self) -> None:
        self.signed: list[tuple[int, str]] = []

    async def get(self, event_id: int) -> dict | None:
        return {"id": event_id, "guild_id": 100, "active": True}

    async def set_signup(self, event: dict, user_id: int, role: str) -> bool:
        self.signed.append((int(event["id"]), role))
        return True


async def test_event_signup_reads_custom_id_from_plain_dict_data() -> None:
    """custom_id лежит в dict, а getattr по dict молча возвращает "".

    Из-за этого кнопки участия отвечали «Ивент не распознан».
    """
    calls: list[str] = []
    events = _FakeEvents()
    interaction = _FakeInteraction({"custom_id": "event:signup:42:going"}, calls)
    interaction.client = SimpleNamespace(services=SimpleNamespace(events=events))

    await _handle_event_signup(interaction, "going")

    assert "Ивент не распознан" not in calls
    assert events.signed == [(42, "going")]


async def test_event_signup_survives_missing_custom_id_data() -> None:
    """У interaction.data может не быть ключа custom_id — это не должно ронять кнопку."""
    calls: list[str] = []
    interaction = _FakeInteraction({}, calls)

    await _handle_event_signup(interaction, "going")

    assert calls == ["Ошибка"]


async def test_confirm_view_disables_all_controls_without_nonexistent_api() -> None:
    view = ConfirmView()

    view._disable_all_items()

    assert view.children
    assert all(getattr(item, "disabled", False) for item in view.children)


class _FakeMessage:
    def __init__(self, message_id: int, fail: Exception | None = None) -> None:
        self.id = message_id
        self.edits: list[dict] = []
        self._fail = fail

    async def edit(self, **kwargs) -> None:
        if self._fail is not None:
            raise self._fail
        self.edits.append(kwargs)


class _FakeChannel:
    def __init__(self, message: _FakeMessage) -> None:
        self._message = message

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        return self._message


class _FakeEventsService:
    def __init__(self) -> None:
        self.embedded: list[dict] = []

    async def embed(self, event: dict) -> discord.Embed:
        self.embedded.append(event)
        return discord.Embed(title=str(event["name"]))


def _event_row(**overrides) -> dict:
    row = {
        "id": 42,
        "guild_id": 100,
        "channel_id": 200,
        "message_id": 300,
        "name": "Тренировка",
    }
    row.update(overrides)
    return row


async def test_migrate_event_cards_replaces_legacy_select_with_buttons() -> None:
    """Старые карточки хранят селект, а discord.py ищет view по custom_id.

    Без перерисовки меню на таких сообщениях не реагирует вовсе.
    """
    message = _FakeMessage(300)
    events_service = _FakeEventsService()
    services = SimpleNamespace(events=events_service)
    bot = SimpleNamespace(get_channel=lambda cid: _FakeChannel(message))

    await _migrate_event_cards(bot, services, [_event_row()])

    assert len(message.edits) == 1
    view = message.edits[0]["view"]
    assert isinstance(view, EventSignupView)
    # Набор компонентов пересобран: селета нет, кнопок столько же, сколько статусов.
    assert all(isinstance(item, discord.ui.Button) for item in view.children)
    assert len(view.children) == len(EVENT_SIGNUP_OPTIONS)


async def test_migrate_event_cards_survives_deleted_message() -> None:
    """Удалённое вручную сообщение не должно ронять миграцию целиком."""
    good = _FakeMessage(300)
    gone = _FakeMessage(999, fail=discord.NotFound(MagicMock(status=404), "Unknown Message"))
    channels = {200: _FakeChannel(gone), 201: _FakeChannel(good)}
    events_service = _FakeEventsService()
    services = SimpleNamespace(events=events_service)
    bot = SimpleNamespace(get_channel=lambda cid: channels[cid])

    await _migrate_event_cards(
        bot, services, [_event_row(message_id=999), _event_row(id=43, channel_id=201, message_id=300)]
    )

    # Удалённое пропустили, а следующее всё равно перерисовали.
    assert len(good.edits) == 1
