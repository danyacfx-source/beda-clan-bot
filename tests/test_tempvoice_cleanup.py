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


def _channel(channel_id=101):
    channel = _VoiceChannel.__new__(_VoiceChannel)
    channel.id = channel_id
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
