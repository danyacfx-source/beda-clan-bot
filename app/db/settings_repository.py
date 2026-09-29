"""Репозиторий настроек серверов."""

from __future__ import annotations

from typing import Any

from app.core.ticket_content import (
    CLAN_INTRO_FOOTER,
    CLAN_INTRO_TEXT,
    CLAN_INTRO_TITLE,
    CLAN_PANEL_BUTTON,
    CLAN_PANEL_EMOJI,
    CLAN_PANEL_FOOTER,
    CLAN_PANEL_TEXT,
    CLAN_PANEL_TITLE,
)
from app.db.base_repository import BaseRepository

DEFAULT_SETTINGS: dict[str, Any] = {
    "log_channel_id": None,
    "ticket_category_id": None,
    "member_log_channel_id": None,
    "message_log_channel_id": None,
    "voice_log_channel_id": None,
    "mod_log_channel_id": None,
    "bot_log_channel_id": None,
    "automod_enabled": 1,
    "blocked_words": "[]",
    "ticket_panel_title": CLAN_PANEL_TITLE,
    "ticket_panel_description": CLAN_PANEL_TEXT,
    "ticket_panel_footer": CLAN_PANEL_FOOTER,
    "ticket_open_label": CLAN_PANEL_BUTTON,
    "ticket_open_emoji": CLAN_PANEL_EMOJI,
    "ticket_intro_title": CLAN_INTRO_TITLE,
    "ticket_intro_description": CLAN_INTRO_TEXT,
    "ticket_intro_footer": CLAN_INTRO_FOOTER,
    "ticket_close_label": "Закрыть тикет",
    "ticket_close_emoji": "🔒",
    "ticket_channel_prefix": "ticket",
}

_INT_COLUMNS = (
    "log_channel_id",
    "ticket_category_id",
    "member_log_channel_id",
    "message_log_channel_id",
    "voice_log_channel_id",
    "mod_log_channel_id",
    "bot_log_channel_id",
)
_SETTING_SQL_COLUMNS = {column: column for column in DEFAULT_SETTINGS}


class SettingsRepository(BaseRepository):
    async def ensure_row(self, guild_id: int) -> None:
        columns = tuple(DEFAULT_SETTINGS)
        column_list = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT OR IGNORE INTO guild_settings (guild_id, {column_list}) VALUES (?, {placeholders})"  # nosec B608
        await self.db.execute(sql, (guild_id, *(DEFAULT_SETTINGS[column] for column in columns)))

    async def get(self, guild_id: int) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            await self.ensure_row(guild_id)
            row = await self.db.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            raise RuntimeError(f"Не удалось создать настройки сервера {guild_id}")
        data = dict(row)
        for column, default in DEFAULT_SETTINGS.items():
            data.setdefault(column, default)
        for column, default in DEFAULT_SETTINGS.items():
            if data.get(column) is None and default is not None:
                data[column] = default
        return data

    async def set(self, guild_id: int, column: str, value: Any) -> None:
        safe_column = _SETTING_SQL_COLUMNS.get(column)
        if safe_column is None:
            raise ValueError(f"Неизвестная колонка настроек: {column}")
        await self.ensure_row(guild_id)
        # safe_column comes exclusively from the fixed DEFAULT_SETTINGS whitelist.
        sql = "UPDATE guild_settings SET " + safe_column + " = ? WHERE guild_id = ?"  # nosec B608
        await self.db.execute(sql, (value, guild_id))

    async def set_defaults_missing(self, guild_id: int) -> dict[str, Any]:
        current = await self.get(guild_id)
        updates: list[tuple[Any, str]] = []
        for column, default in DEFAULT_SETTINGS.items():
            if current.get(column) is None and default is not None:
                updates.append((default, column))
        for value, column in updates:
            await self.set(guild_id, column, value)
        return await self.get(guild_id)
