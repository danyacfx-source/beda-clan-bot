"""Protocol-контракты портов — границы, через которые потребители к касательства к слоям.

Порты структурно совпадают с реальными классами app/ (проверяется в
app/core/adapters.py и в тестах). Это позволяет подменять реализацию
(тесты, фейки, другая инфраструктура) без изменения потребителей.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KvStore(Protocol):
    """Хранилище «ключ-значение» (реальный кандидат: KvRepository)."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> None: ...
    async def delete(self, key: str) -> None: ...


@runtime_checkable
class SettingsStore(Protocol):
    """Настройки гильдии (реальный кандидат: SettingsService)."""

    async def get(self, guild_id: int) -> dict[str, object]: ...
    async def update(self, guild_id: int, **values: object) -> None: ...


@runtime_checkable
class LogSink(Protocol):
    """Аудиты/логи в Discord-каналы (реальный кандидат: LoggingService)."""

    async def send_embed(self, guild: object, embed: object, *, category: str | None = None) -> None: ...
