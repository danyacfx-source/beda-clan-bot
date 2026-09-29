"""Тесты удаления временных голосовых каналов при выходе пользователя."""
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
from discord import HTTPException

from app.cogs.tempvoice.tempvoice import TempVoiceCog

_VoiceChannel = type("VoiceChannel2", (discord.VoiceChannel,), {})


def _cog():
    cog = TempVoiceCog.__new__(TempVoiceCog)
    cog.bot = MagicMock()
    cog.tempvoice = MagicMock()
    cog.tempvoice.delete = AsyncMock()
    config = MagicMock()
    config.temp_voice_trigger_ids = ()
    config.temp_voice_category_id = None
    cog.bot.config = config
    return cog


def _state(channel):
    state = MagicMock()
    state.channel = channel
    return state


def _real_state(channel=None):
    """Настоящий discord.VoiceState: атрибута guild у него нет в принципе.

    Конструктор требует payload от Discord, поэтому объект создаётся в обход
    него — важен именно тип, а не заполненные поля.
    """
    state = object.__new__(discord.VoiceState)
    state.channel = channel
    return state


def _channel(channel_id=101):
    channel = _VoiceChannel.__new__(_VoiceChannel)
    channel.id = channel_id
    channel.category_id = None
    channel.delete = AsyncMock()
    return channel


@patch("app.cogs.tempvoice.tempvoice.discord.VoiceChannel", new=_VoiceChannel)
async def test_empty_temp_channel_deleted_on_leave():
    cog = _cog()
    channel = _channel(101)
    cog.bot.get_channel.return_value = channel
    cog.tempvoice.owner_of = AsyncMock(return_value=777)
    cog.tempvoice.channel_of_owner = AsyncMock(return_value=None)

    with patch.object(_VoiceChannel, "members", new_callable=PropertyMock, return_value=[]):
        await cog.on_voice_state_update(MagicMock(), _state(channel), _state(None))

    cog.tempvoice.owner_of.assert_awaited_once_with(101)
    channel.delete.assert_awaited_once()
    cog.tempvoice.delete.assert_awaited_once_with(101)


@patch("app.cogs.tempvoice.tempvoice.discord.VoiceChannel", new=_VoiceChannel)
async def test_nonempty_temp_channel_not_deleted_on_leave():
    cog = _cog()
    channel = _channel(102)
    cog.tempvoice.owner_of = AsyncMock(return_value=888)

    with patch.object(_VoiceChannel, "members", new_callable=PropertyMock, return_value=[MagicMock()]):
        await cog.on_voice_state_update(MagicMock(), _state(channel), _state(None))

    cog.tempvoice.owner_of.assert_not_awaited()
    channel.delete.assert_not_awaited()


@patch("app.cogs.tempvoice.tempvoice.discord.VoiceChannel", new=_VoiceChannel)
async def test_delete_failure_keeps_db_row():
    cog = _cog()
    channel = _channel(101)
    channel.delete.side_effect = HTTPException(MagicMock(), "fail")
    cog.bot.get_channel.return_value = channel

    await cog._remove_channel(101)

    cog.tempvoice.delete.assert_not_awaited()


async def test_настоящий_voice_state_не_ломает_обработчик():
    """Раньше гильдия бралась из after.guild, но у VoiceState нет атрибута guild.

    MagicMock в остальных тестах скрывал баг: он отвечает на любой атрибут.
    """
    cog = _cog()
    cog._tempvoice_config = AsyncMock(return_value={"trigger_ids": (), "category_id": None})
    member = MagicMock()
    member.guild.id = 653949456630153216

    # Настоящие VoiceState — именно они приходят от discord.py.
    await cog.on_voice_state_update(member, _real_state(), _real_state())

    cog._tempvoice_config.assert_awaited_once_with(653949456630153216)


@patch("app.cogs.tempvoice.tempvoice.discord.VoiceChannel", new=_VoiceChannel)
async def test_вход_в_триггер_создаёт_временный_канал():
    cog = _cog()
    cog._tempvoice_config = AsyncMock(return_value={"trigger_ids": (101,), "category_id": None})
    cog.tempvoice.channel_of_owner = AsyncMock(return_value=None)
    cog.tempvoice.create = AsyncMock()

    trigger = _channel(101)
    trigger.guild = MagicMock()
    created = _channel(555)
    created.send = AsyncMock()
    trigger.guild.create_voice_channel = AsyncMock(return_value=created)

    member = MagicMock()
    member.id = 4242
    member.display_name = "Тест"
    member.guild.id = 653949456630153216
    member.move_to = AsyncMock()

    after = _real_state()
    after.channel = trigger

    await cog.on_voice_state_update(member, _real_state(), after)

    trigger.guild.create_voice_channel.assert_awaited_once()
    cog.tempvoice.create.assert_awaited_once()
    member.move_to.assert_awaited_once_with(created)

