"""Тесты мастера объявлений: хранение адресата и пошаговый диалог в личке."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from app.cogs.utility.announce import AnnounceCog, can_publish
from app.core.loader import COG_PROVIDERS
from app.db.database import Database
from app.db.kv_repository import KvRepository
from app.services.announce_service import AnnounceService, AnnounceTarget

GUILD_ID = 4242
CHANNEL_ID = 777
USER_ID = 31337
KEY = f"announce:target:{USER_ID}"


# --- инфраструктура фейков ---


def _dm_channel() -> tuple[discord.DMChannel, list[dict]]:
    """Личный чат: мок со спекой DMChannel, чтобы прошёл isinstance-проверка кога."""
    sent: list[dict] = []

    async def send(content=None, *, files=None, **kwargs):
        sent.append({"content": content, "files": files, **kwargs})
        return SimpleNamespace(id=999)

    channel = MagicMock(spec=discord.DMChannel)
    channel.send = send
    return channel, sent


class _Channel:
    """Канал сервера — адресат объявления."""

    def __init__(self, channel_id: int = CHANNEL_ID, name: str = "announcements") -> None:
        self.id = channel_id
        self.name = name
        self.sent: list[dict] = []

    async def send(self, content=None, *, files=None, **kwargs):
        self.sent.append({"content": content, "files": files, **kwargs})
        return SimpleNamespace(id=999)

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"


class _Attachment:
    def __init__(self) -> None:
        self.file = "<file>"

    async def to_file(self):
        return self.file


def _message(content: str, *, attachments=()):
    """Сообщение в личке. Отправленные ответы — в ``message.sent``."""
    channel, sent = _dm_channel()
    message = SimpleNamespace(
        content=content,
        attachments=list(attachments),
        author=SimpleNamespace(id=USER_ID, bot=False),
        channel=channel,
        sent=sent,
    )
    return message


def _guild(*, guild_id: int = GUILD_ID, channel: _Channel | None = None, publishable: bool = True):
    channel = channel or _Channel()
    member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_guild=publishable, administrator=False)
    )
    return SimpleNamespace(
        id=guild_id,
        name="BEDA",
        text_channels=[channel],
        get_member=lambda _uid: member,
        get_channel=lambda cid: channel if cid == channel.id else None,
    )


async def _cog(service, *, guild=None, fetchable: _Channel | None = None) -> AnnounceCog:
    async def fetch_channel(_channel_id):
        if fetchable is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel")
        return fetchable

    bot = SimpleNamespace(
        guilds=[guild] if guild is not None else [],
        get_guild=lambda gid: guild if (guild is not None and gid == guild.id) else None,
        fetch_channel=fetch_channel,
    )
    return AnnounceCog(bot, service)


# --- сервис ---


@pytest.fixture
async def db(tmp_path):
    database = Database(os.path.join(tmp_path, "test.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def service(db):
    return AnnounceService(KvRepository(db))


async def test_target_survives_a_fresh_service(service, db) -> None:
    """Адресат лежит в БД, а не в памяти — мастер переживает рестарт бота."""
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))

    reloaded = AnnounceService(KvRepository(db))

    assert await reloaded.get_target(USER_ID) == AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID)


async def test_clear_target_forgets_the_channel(service) -> None:
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))

    await service.clear_target(USER_ID)

    assert await service.get_target(USER_ID) is None


async def test_targets_do_not_leak_between_users(service) -> None:
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))

    assert await service.get_target(USER_ID + 1) is None


async def test_corrupted_record_is_discarded_instead_of_raising(service, db) -> None:
    await KvRepository(db).set(KEY, "не json")

    assert await service.get_target(USER_ID) is None
    assert await KvRepository(db).get(KEY) is None


def test_announce_service_is_available_to_cog_loader() -> None:
    """Ког собирается загрузчиком по таблице провайдеров."""
    assert "announce" in COG_PROVIDERS


# --- права ---


def test_can_publish_requires_manage_guild_or_admin() -> None:
    assert not can_publish(SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False, administrator=False)))
    assert can_publish(SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True, administrator=False)))
    assert can_publish(SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False, administrator=True)))


# --- ког: запуск мастера ---


async def test_start_trigger_offers_a_guild_picker(service) -> None:
    cog = await _cog(service, guild=_guild())

    message = _message("старт")
    await cog.on_message(message)

    assert len(message.sent) == 1
    assert message.sent[0]["view"] is not None


async def test_start_trigger_accepts_case_and_slash_variants(service) -> None:
    for text in ("Старт", "START", "/start"):
        cog = await _cog(service, guild=_guild())
        message = _message(text)

        await cog.on_message(message)

        assert "view" in message.sent[0], text


async def test_start_without_admin_rights_is_refused(service) -> None:
    cog = await _cog(service, guild=_guild(publishable=False))

    message = _message("старт")
    await cog.on_message(message)

    assert "view" not in message.sent[0]
    assert await service.get_target(USER_ID) is None


async def test_unrelated_dm_message_is_ignored(service) -> None:
    cog = await _cog(service, guild=_guild())

    message = _message("просто болтовня")
    await cog.on_message(message)

    assert message.sent == []


# --- ког: шаги 3 и 4 ---


async def test_text_step_asks_for_an_image(service) -> None:
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))
    cog = await _cog(service, guild=_guild())

    message = _message("Собрание в 19:00")
    await cog.on_message(message)

    assert len(message.sent) == 1
    assert "embed" in message.sent[0]


async def test_text_and_image_publish_immediately(service) -> None:
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))
    target = _Channel()
    cog = await _cog(service, guild=_guild(channel=target))

    await cog.on_message(_message("Собрание в 19:00", attachments=[_Attachment()]))

    assert target.sent == [{"content": "Собрание в 19:00", "files": ["<file>"]}]


async def test_skip_image_publishes_text_only(service) -> None:
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))
    target = _Channel()
    cog = await _cog(service, guild=_guild(channel=target))

    await cog.on_message(_message("Собрание в 19:00"))
    cog._state[USER_ID]["phase"] = "image"
    await cog.on_message(_message("-"))

    assert target.sent[-1]["content"] == "Собрание в 19:00"
    assert target.sent[-1]["files"] is None


async def test_publishing_clears_the_saved_target(service) -> None:
    """После публикации мастер закрыт — иначе следующее сообщение уйдёт в канал."""
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))
    target = _Channel()
    cog = await _cog(service, guild=_guild(channel=target))

    await cog.on_message(_message("Текст", attachments=[_Attachment()]))

    assert await service.get_target(USER_ID) is None
    assert USER_ID not in cog._state


async def test_empty_announce_is_cancelled(service) -> None:
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))
    target = _Channel()
    cog = await _cog(service, guild=_guild(channel=target))

    await cog.on_message(_message("-"))
    cog._state[USER_ID]["phase"] = "image"
    await cog.on_message(_message("-"))

    assert target.sent == []
    assert await service.get_target(USER_ID) is None


async def test_unreachable_channel_closes_the_wizard(service) -> None:
    """Канал исчез, пока мастер был открыт: сообщение об ошибке, не падение."""
    await service.set_target(USER_ID, AnnounceTarget(guild_id=GUILD_ID, channel_id=CHANNEL_ID))
    cog = await _cog(service, guild=None)

    message = _message("Текст", attachments=[_Attachment()])
    await cog.on_message(message)

    assert len(message.sent) == 1
    assert await service.get_target(USER_ID) is None
