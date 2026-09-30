"""Регрессионные тесты интерактивных представлений."""

from types import SimpleNamespace

from app.core.views import ConfirmView, _handle_event_signup


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
