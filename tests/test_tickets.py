"""Тесты тикетов: создание, закрытие и сохранение транскрипта в БД.

Транскрипт обязан переживать удаление канала — иначе после закрытия
историю тикета уже нигде не посмотреть (ключевой фикс веб-панели).
"""
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.config import Config
from app.core.composition import assemble
from app.db.database import Database

GILD_ID = 1234


def _config(tmp: str) -> Config:
    return Config(
        token="x",
        prefix="!",
        db_path=os.path.join(tmp, "bot.db"),
        log_level="ERROR",
        status_activity="s",
        owner_id=None,
    )


async def _setup(tmp: str):
    """Сборка сервисов с отдельной БД; возвращает (tickets, db)."""
    db = Database(os.path.join(tmp, "bot.db"))
    await db.connect()
    root = assemble(config=_config(tmp), db=db)
    return root.services.tickets, db


class _History:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __aiter__(self):
        async def _gen():
            for text in self._lines:
                msg = MagicMock()
                msg.clean_content = text
                msg.created_at = datetime.now(UTC)
                msg.author = SimpleNamespace(id=1)
                msg.author.__str__ = lambda self: "user"
                yield msg

        return _gen()


def _channel(channel_id: int, *, lines: list[str] | None = None) -> MagicMock:
    channel = MagicMock()
    channel.id = channel_id
    channel.name = f"ticket-{channel_id}"
    channel.mention = f"<#{channel_id}>"
    channel.__str__ = lambda self: self.name
    channel.history.return_value = _History(lines or ["Привет, помогите!", "Спасибо!"])
    channel.send = AsyncMock()
    channel.delete = AsyncMock()
    return channel


def _guild(channels: list[MagicMock]) -> MagicMock:
    guild = MagicMock()
    guild.id = GILD_ID
    guild.name = "Test Guild"
    guild.default_role = MagicMock(id=0)
    guild.me = MagicMock(id=99)
    by_id = {c.id: c for c in channels}
    guild.get_channel.side_effect = lambda cid: by_id.get(cid)
    guild.create_text_channel = AsyncMock(side_effect=lambda **_: None)
    return guild


class _Creator:
    """Хешируемый фейк участника (в create он — ключ в overwrites)."""

    def __init__(self, user_id: int, name: str) -> None:
        self.id = user_id
        self.name = name
        self.mention = f"<@{user_id}>"

    def __hash__(self) -> int:
        return self.id

    def __str__(self) -> str:
        return self.name


async def test_create_and_persist_transcript(tmp_path) -> None:
    tickets, db = await _setup(str(tmp_path))
    try:
        channel = _channel(555)
        guild = _guild([channel])
        guild.create_text_channel = AsyncMock(return_value=channel)
        creator = _Creator(42, "vasya")

        result = await tickets.create(guild, creator)
        assert result.error is None
        assert result.channel is channel
        guild.create_text_channel.assert_awaited_once()

        ticket = await tickets.get_open_ticket(GILD_ID, channel.id)
        assert ticket is not None
        assert ticket["status"] == "open"
        assert ticket["creator_id"] == 42

        closed = await tickets.close_by_id(guild, ticket["ticket_id"], MagicMock(id=99, mention="<@99>"))
        assert closed is not None and closed.error is None

        stored = await tickets.get_ticket(ticket["ticket_id"])
        assert stored["status"] == "closed"
        assert stored["closed_at"] is not None
        assert stored["transcript"] is not None
        assert "Привет, помогите!" in stored["transcript"]
        assert "Спасибо!" in stored["transcript"]

        rows = await tickets.list_tickets(GILD_ID)
        assert len(rows) == 1
        assert rows[0]["status"] == "closed"
    finally:
        await db.close()


async def test_close_missing_ticket(tmp_path) -> None:
    tickets, db = await _setup(str(tmp_path))
    try:
        guild = _guild([])
        result = await tickets.close_by_id(guild, 9999, MagicMock(id=99, mention="<@99>"))
        assert result is not None and result.error is not None
    finally:
        await db.close()


async def test_has_open_limits_creator(tmp_path) -> None:
    tickets, db = await _setup(str(tmp_path))
    try:
        channel1 = _channel(555)
        guild = _guild([channel1])
        guild.create_text_channel = AsyncMock(return_value=channel1)
        creator = _Creator(42, "vasya")

        assert (await tickets.create(guild, creator)).error is None

        channel2 = _channel(556)
        guild.create_text_channel = AsyncMock(return_value=channel2)
        second = await tickets.create(guild, creator)
        assert second.error is not None
        assert "открытый тикет" in second.error

        # другой автор может открыть снова
        other = _Creator(43, "petya")
        third = await tickets.create(guild, other)
        assert third.error is None
    finally:
        await db.close()


async def test_create_uses_editable_texts(tmp_path) -> None:
    from app.core.views import TicketCloseView

    tickets, db = await _setup(str(tmp_path))
    try:
        await tickets._settings.update(
            GILD_ID,
            ticket_channel_prefix="support",
            ticket_intro_title="Помощь",
            ticket_intro_description="Пишите сюда, {member}",
            ticket_close_label="Завершить",
            ticket_close_emoji="",
        )
        channel = _channel(557)
        guild = _guild([channel])
        guild.create_text_channel = AsyncMock(return_value=channel)
        creator = _Creator(42, "vasya")

        result = await tickets.create(guild, creator)
        assert result.error is None and result.channel is channel

        call_name = guild.create_text_channel.call_args.kwargs.get("name")
        assert call_name.startswith("support-")

        send_kwargs = channel.send.call_args.kwargs
        embed = send_kwargs.get("embed")
        assert embed.title == "Помощь"
        assert "Пишите сюда, <@42>" in embed.description
        view = send_kwargs.get("view")
        assert isinstance(view, TicketCloseView)
        assert view.close_ticket.label == "Завершить"
        assert view.close_ticket.emoji is None
    finally:
        await db.close()


async def test_open_view_applies_custom_label():
    from app.core.views import TicketOpenView

    view = TicketOpenView(MagicMock(), label="Проблема?", emoji="")
    assert view.open_ticket.label == "Проблема?"
    assert view.open_ticket.emoji is None
