"""Сервис настроек модулей: отдаёт когам эффективные значения.

Коги и веб-панель работают не с ``Config`` напрямую, а с этим сервисом.
Эффективное значение — переопределение из БД, иначе значение из ``.env``.
Поэтому настройка, заданная в панели, применяется сразу и без рестарта,
а удаление переопределения возвращает поведение ``.env``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.module_settings import (
    MODULE_SPECS,
    SPEC_BY_KEY,
    field_by_key,
    normalize,
    to_jsonable,
)

logger = logging.getLogger("bot.services.module_settings")


class ModuleSettingsService:
    def __init__(self, repository: Any, config: Any) -> None:
        self._repo = repository
        self._config = config
        # guild_id -> {module: overrides}. Кэш освобождается на invalidate().
        self._cache: dict[int, dict[str, dict[str, Any]]] = {}
        self._defaults: dict[str, dict[str, Any]] = {}

    # --- Дефолты из .env -------------------------------------------------

    @property
    def specs(self) -> tuple[Any, ...]:
        return MODULE_SPECS

    def defaults(self, module: str) -> dict[str, Any]:
        if module not in self._defaults:
            from app.core.module_settings import defaults_for

            self._defaults[module] = defaults_for(module, self._config)
        return dict(self._defaults[module])

    def all_defaults(self) -> dict[str, dict[str, Any]]:
        return {module: self.defaults(module) for module in SPEC_BY_KEY}

    # --- Чтение ----------------------------------------------------------

    async def _overrides(self, guild_id: int) -> dict[str, dict[str, Any]]:
        cached = self._cache.get(guild_id)
        if cached is None:
            try:
                cached = await self._repo.get_all(guild_id)
            except Exception:
                logger.exception("Не удалось прочитать настройки модулей сервера %s", guild_id)
                cached = {}
            self._cache[guild_id] = cached
        return cached

    async def get(self, guild_id: int, module: str) -> dict[str, Any]:
        """Эффективные значения модуля: БД поверх ``.env``."""
        values = self.defaults(module)
        values.update((await self._overrides(guild_id)).get(module, {}))
        return values

    async def all_modules(self, guild_id: int) -> dict[str, dict[str, Any]]:
        overrides = await self._overrides(guild_id)
        result: dict[str, dict[str, Any]] = {}
        for module in SPEC_BY_KEY:
            values = self.defaults(module)
            values.update(overrides.get(module, {}))
            result[module] = values
        return result

    async def overrides(self, guild_id: int) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in (await self._overrides(guild_id)).items()}

    # --- Запись ----------------------------------------------------------

    async def update(self, guild_id: int, module: str, values: dict[str, Any]) -> dict[str, Any]:
        """Проверяет и сохраняет переопределения модуля. Возвращает эффективные значения."""
        if module not in SPEC_BY_KEY:
            raise ValueError(f"Неизвестный модуль: {module}")
        clean: dict[str, Any] = {}
        for key, raw in values.items():
            field = field_by_key(module, key)
            if field is None:
                raise ValueError(f"Неизвестная настройка: {module}.{key}")
            clean[key] = to_jsonable(normalize(field, raw))
        current = dict((await self._overrides(guild_id)).get(module, {}))
        current.update(clean)
        if current:
            await self._repo.set_values(guild_id, module, current)
        else:
            await self._repo.reset(guild_id, module)
        self._cache.pop(guild_id, None)
        logger.info("Настройки модуля %s обновлены для сервера %s: %s", module, guild_id, ", ".join(clean) or "-")
        return await self.get(guild_id, module)

    async def reset(self, guild_id: int, module: str | None = None) -> None:
        await self._repo.reset(guild_id, module)
        self._cache.pop(guild_id, None)

    def invalidate(self, guild_id: int | None = None) -> None:
        if guild_id is None:
            self._cache.clear()
        else:
            self._cache.pop(guild_id, None)
