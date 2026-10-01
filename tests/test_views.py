"""Регрессионные тесты интерактивных представлений."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from discord import app_commands

from app.core import checks
from app.core.loader import _migrate_event_cards
from app.core.views import (
    EVENT_SIGNUP_OPTIONS,
    ConfirmView,
    EventSignupView,
    _caller_room_access,
    _handle_event_signup,
)


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


@dataclass(frozen=True)
class _FakeOverwriteTarget:
    """Хешируемая замена роли/участника: ключ в overwrites."""

    id: int
    name: str = ""


@dataclass
class _FakeOverwriteGuild:
    id: int
    default_role: _FakeOverwriteTarget
    me: _FakeOverwriteTarget
    roles: dict[int, _FakeOverwriteTarget]

    def get_role(self, role_id: int) -> _FakeOverwriteTarget | None:
        return self.roles.get(role_id)


async def test_caller_room_is_closed_for_everyone_and_open_for_allowed_roles() -> None:
    """@everyone не должен видеть и подключаться, иначе комната открыта всем."""
    squad_a = _FakeOverwriteTarget(id=1048020872667091035, name="Squad **A**")
    squad_b = _FakeOverwriteTarget(id=1545083746988851261, name="Squad B")
    everyone = _FakeOverwriteTarget(id=555, name="@everyone")
    bot_member = _FakeOverwriteTarget(id=999, name="BEDA")
    guild = _FakeOverwriteGuild(
        id=100, default_role=everyone, me=bot_member, roles={squad_a.id: squad_a, squad_b.id: squad_b}
    )
    config = SimpleNamespace(where_play_caller_role_ids=(1048020872667091035, 1545083746988851261))

    allowed, overwrites = _caller_room_access(guild, config)

    assert {role.id for role in allowed} == {squad_a.id, squad_b.id}
    # Discord отклоняет не-Mapping с TypeError: «overwrites parameter expects a dict».
    assert isinstance(overwrites, Mapping)
    assert overwrites[everyone].view_channel is False
    assert overwrites[everyone].connect is False
    for role in (squad_a, squad_b):
        assert overwrites[role].view_channel is True
        assert overwrites[role].connect is True
    # Боту нужны права, иначе он не сможет перенести участника и убрать комнату.
    assert overwrites[bot_member].connect is True
    assert overwrites[bot_member].move_members is True
    assert overwrites[bot_member].manage_channels is True


async def test_caller_room_overwrites_match_what_discord_accepts() -> None:
    """Проверяем ровно то требование, из-за которого канал падал.

    Guild._create_channel принимает Mapping и только его: список кортежей
    вызывал TypeError в проде, а тесты этого не видели.
    """
    squad_a = _FakeOverwriteTarget(id=1048020872667091035, name="Squad A")
    guild = _FakeOverwriteGuild(
        id=100,
        default_role=_FakeOverwriteTarget(id=555, name="@everyone"),
        me=_FakeOverwriteTarget(id=999, name="BEDA"),
        roles={squad_a.id: squad_a},
    )
    config = SimpleNamespace(where_play_caller_role_ids=(1048020872667091035,))

    _, overwrites = _caller_room_access(guild, config)

    # Условие из discord.py: не Mapping -> TypeError.
    assert not isinstance(overwrites, (list, tuple))
    assert isinstance(overwrites, Mapping)
    for target, perm in overwrites.items():
        assert isinstance(perm, discord.PermissionOverwrite)


async def test_caller_room_ignores_roles_missing_on_guild() -> None:
    """Роль из конфига могла быть удалена: тогда её нельзя ставить в overwrites."""
    squad_a = _FakeOverwriteTarget(id=1048020872667091035, name="Squad A")
    guild = _FakeOverwriteGuild(
        id=100,
        default_role=_FakeOverwriteTarget(id=555, name="@everyone"),
        me=_FakeOverwriteTarget(id=999, name="BEDA"),
        roles={squad_a.id: squad_a},
    )
    config = SimpleNamespace(where_play_caller_role_ids=(1048020872667091035, 1545083746988851261))

    allowed, overwrites = _caller_room_access(guild, config)

    assert [role.id for role in allowed] == [squad_a.id]
    assert isinstance(overwrites, Mapping)
    # @everyone закрыт, бот разрешён, найденная роль разрешена.
    assert len(overwrites) == 3


async def test_caller_room_stays_open_when_no_roles_configured() -> None:
    """Пустой конфиг не должен ломать кнопку: канал создаётся как раньше.

    None, а не пустой словарь: пустой набор прав запретил бы каналу
    наследовать категорию, и в комнату не попал бы никто.
    """
    guild = _FakeOverwriteGuild(
        id=100,
        default_role=_FakeOverwriteTarget(id=555, name="@everyone"),
        me=_FakeOverwriteTarget(id=999, name="BEDA"),
        roles={},
    )
    config = SimpleNamespace(where_play_caller_role_ids=())

    allowed, overwrites = _caller_room_access(guild, config)

    assert allowed == []
    assert overwrites is None


@dataclass
class _FakeCheckMember:
    """Подделка участника для проверок доступа."""

    id: int
    guild_permissions: SimpleNamespace
    roles: tuple = ()


def _check_interaction(member, config, guild_id: int = 100, section_role_id: int | None = None) -> SimpleNamespace:
    squad = _FakeOverwriteTarget(id=1048020872667091035, name="Squad **A**")
    known = {squad.id: squad}
    if section_role_id is not None:
        known[section_role_id] = _FakeOverwriteTarget(id=section_role_id, name="Коллеры")
    guild = SimpleNamespace(
        id=guild_id,
        default_role=_FakeOverwriteTarget(id=555, name="@everyone"),
        me=_FakeOverwriteTarget(id=999, name="BEDA"),
        get_role=lambda rid: known.get(rid),
    )
    client = SimpleNamespace(config=config)
    if section_role_id is not None:

        async def _row(_guild_id: int) -> dict:
            return {"role_id": section_role_id}

        client.services = SimpleNamespace(where_play=SimpleNamespace(row=_row))
    return SimpleNamespace(
        client=client,
        guild=guild,
        guild_id=guild_id,
        user=member,
    )


async def _run_check(member, config, section_role_id: int | None = None) -> bool:
    """Гоняет предикат из checks.requires_role.

    app_commands.check не декоратор в полном смысле: он вешает функцию в
    атрибут __discord_app_commands_checks__.
    """
    from app.cogs.events.where_play import _section_role_ids

    async def command(interaction) -> None:  # noqa: ARG001
        pass

    checks.requires_role("where_play_command_role_ids", _section_role_ids)(command)
    (predicate,) = command.__discord_app_commands_checks__
    return await predicate(_check_interaction(member, config, section_role_id=section_role_id))


async def test_where_play_command_allowed_for_role_from_setup() -> None:
    """Роль, выданная через /setup_where_play, обязана пускать и снаружи.

    Внешняя проверка смотрела только на конфиг и резала роль коллеров, хотя
    внутренняя is_manager её пропускала: человек получал «Недостаточно прав»
    при положенном доступе.
    """
    config = SimpleNamespace(where_play_command_role_ids=(1048020872667091035,))
    member = _FakeCheckMember(
        id=7,
        guild_permissions=SimpleNamespace(administrator=False),
        roles=(_FakeOverwriteTarget(id=1545083746988851261, name="Коллеры"),),
    )

    assert await _run_check(member, config, section_role_id=1545083746988851261) is True


async def test_where_play_command_denied_without_any_known_role() -> None:
    """Роль не из конфига и не из /setup_where_play доступа не даёт."""
    config = SimpleNamespace(where_play_command_role_ids=(1048020872667091035,))
    member = _FakeCheckMember(
        id=8,
        guild_permissions=SimpleNamespace(administrator=False),
        roles=(_FakeOverwriteTarget(id=1545083746988851261, name="Коллеры"),),
    )

    with pytest.raises(discord.app_commands.CheckFailure):
        await _run_check(member, config, section_role_id=None)


async def test_where_play_command_allowed_for_configured_role() -> None:
    config = SimpleNamespace(where_play_command_role_ids=(1048020872667091035,))
    member = _FakeCheckMember(
        id=1,
        guild_permissions=SimpleNamespace(administrator=False),
        roles=(_FakeOverwriteTarget(id=1048020872667091035, name="Squad A"),),
    )

    assert await _run_check(member, config) is True


async def test_where_play_command_denies_outsider() -> None:
    config = SimpleNamespace(where_play_command_role_ids=(1048020872667091035,))
    member = _FakeCheckMember(
        id=2,
        guild_permissions=SimpleNamespace(administrator=False),
        roles=(_FakeOverwriteTarget(id=1545083746988851261, name="Squad B"),),
    )

    with pytest.raises(app_commands.CheckFailure) as exc:
        await _run_check(member, config)
    # Название роли экранируется, иначе «**» в имени ломает разметку.
    assert "Squad \\*\\*A\\*\\*" in str(exc.value)


async def test_where_play_command_allows_administrator_without_role() -> None:
    """Иначе ограничение по роли заперло бы снаружи того, кто может настроить."""
    config = SimpleNamespace(where_play_command_role_ids=(1048020872667091035,))
    member = _FakeCheckMember(id=3, guild_permissions=SimpleNamespace(administrator=True), roles=())

    assert await _run_check(member, config) is True


async def test_where_play_command_open_when_config_empty() -> None:
    config = SimpleNamespace(where_play_command_role_ids=())
    member = _FakeCheckMember(id=4, guild_permissions=SimpleNamespace(administrator=False), roles=())

    assert await _run_check(member, config) is True


async def test_config_role_passes_inner_is_manager_check() -> None:
    """Роль из конфига обязана проходить и внутреннюю проверку is_manager.

    Иначе участник проходит проверку команды, попадает внутрь и там же
    получает «Недостаточно прав» — доступ вроде есть, а на деле нет.
    """
    from app.services.where_play_service import WherePlayService

    service = WherePlayService.__new__(WherePlayService)
    member = SimpleNamespace(
        id=1,
        guild_permissions=SimpleNamespace(administrator=False),
        roles=(SimpleNamespace(id=1048020872667091035, name="Squad A"),),
    )

    # Роль коллера из карточки не совпадает с конфиг-ролью.
    assert service.is_manager({"role_id": 1545083746988851261}, member) is False
    assert (
        service.is_manager({"role_id": 1545083746988851261}, member, (1048020872667091035,)) is True
    )


async def test_where_play_commands_all_carry_role_check() -> None:
    """Ограничение должно висеть на всех командах раздела, а не на одной."""
    from app.cogs.events.where_play import WherePlayCog

    for name in ("where_play", "stop_play", "where_play_status", "where_play_card"):
        command = getattr(WherePlayCog, name)
        assert getattr(command, "checks", None), f"у команды {name} нет проверок"

    # setup_where_play остаётся только для админа: это первоначальная настройка.
    setup = getattr(WherePlayCog, "setup_where_play", None)
    if setup is not None:
        assert setup.default_permissions.administrator is True


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
