"""Репозиторий переопределений настроек модулей.

Значение — JSON-объект с полями одного модуля. Одна строка на пару
(сервер, модуль): так чтение всех модулей — один запрос, а не N.
"""

from __future__ import annotations

import json
from typing import Any

from app.db.base_repository import BaseRepository

_TABLE = "module_settings"

#: Таблица и её колонки фиксированы, имена приходят только из реестра.
_SQL = f"SELECT module, value FROM {_TABLE} WHERE guild_id = ?"
_SQL_ONE = f"SELECT value FROM {_TABLE} WHERE guild_id = ? AND module = ?"
_SQL_UPSERT = (
    f"INSERT INTO {_TABLE} (guild_id, module, value) VALUES (?, ?, ?) "
    f"ON CONFLICT(guild_id, module) DO UPDATE SET value = excluded.value"
)
_SQL_DELETE = f"DELETE FROM {_TABLE} WHERE guild_id = ? AND module = ?"
_SQL_DELETE_ALL = f"DELETE FROM {_TABLE} WHERE guild_id = ?"


class ModuleSettingsRepository(BaseRepository):
    async def get_all(self, guild_id: int) -> dict[str, dict[str, Any]]:
        rows = await self.db.fetchall(_SQL, (guild_id,))
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            module = data.get("module")
            if not module:
                continue
            try:
                parsed = json.loads(data.get("value") or "{}")
            except ValueError:
                continue
            if isinstance(parsed, dict):
                result[str(module)] = parsed
        return result

    async def set_values(self, guild_id: int, module: str, values: dict[str, Any]) -> None:
        await self.db.execute(_SQL_UPSERT, (guild_id, module, json.dumps(values, ensure_ascii=False)))

    async def reset(self, guild_id: int, module: str | None = None) -> None:
        if module is None:
            await self.db.execute(_SQL_DELETE_ALL, (guild_id,))
        else:
            await self.db.execute(_SQL_DELETE, (guild_id, module))
