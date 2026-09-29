"""Базовый сервис с graceful degradation."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from app.db.base_repository import BaseRepository

logger = logging.getLogger("bot.services")

T = TypeVar("T")


class BaseService(ABC):
    """Базовый сервис с fallback на in-memory кеш при потере БД.

    Предоставляет:
    - Автоматическое переключение на кеш при ошибках БД
    - Автоматическое восстановление при успехе
    - Логирование всех переключений
    """

    def __init__(self, repo: BaseRepository | None = None) -> None:
        self._repo = repo
        self._cache: dict[str, Any] = {}
        self._db_available = True
        self._last_error: str | None = None
        self._error_count = 0

    @property
    def is_db_available(self) -> bool:
        """Доступна ли БД."""
        return self._db_available

    async def _safe_db_call(
        self,
        coro: Any,
        fallback: T | None = None,
        cache_key: str | None = None,
        cache_ttl: float = 300.0,
    ) -> T | None:
        """Выполняет операцию с БД с fallback на кеш.

        Args:
            coro: Coroutine для выполнения
            fallback: Значение по умолчанию при ошибке
            cache_key: Ключ для кеширования результата
            cache_ttl: Время жизни кеша в секундах

        Returns:
            Результат операции, значение из кеша или fallback
        """
        try:
            result = await coro
            self._db_available = True
            self._error_count = 0
            self._last_error = None

            if cache_key is not None:
                self._cache[cache_key] = {
                    "value": result,
                    "expires": asyncio.get_event_loop().time() + cache_ttl,
                }

            return result

        except Exception as e:
            self._error_count += 1
            self._last_error = str(e)[:500]

            # Mark DB as unavailable after 3 consecutive errors
            if self._error_count >= 3:
                if self._db_available:
                    logger.error("Service %s: DB unavailable after %d errors: %s", self.__class__.__name__, self._error_count, e)
                self._db_available = False

            # Try to return cached value
            if cache_key is not None and cache_key in self._cache:
                cached = self._cache[cache_key]
                now = asyncio.get_event_loop().time()

                if now < cached["expires"]:
                    logger.warning("Service %s: DB error, returning cached value: %s", self.__class__.__name__, str(e)[:100])
                    return cached["value"]
                else:
                    # Expired, remove from cache
                    del self._cache[cache_key]

            logger.warning("Service %s: DB error, returning fallback: %s", self.__class__.__name__, str(e)[:100])
            return fallback

    def _get_cached(self, key: str) -> Any | None:
        """Получает значение из кеша."""
        if key not in self._cache:
            return None

        cached = self._cache[key]
        now = asyncio.get_event_loop().time()

        if now < cached["expires"]:
            return cached["value"]
        else:
            del self._cache[key]
            return None

    def _set_cached(self, key: str, value: Any, ttl: float = 300.0) -> None:
        """Сохраняет значение в кеш."""
        now = asyncio.get_event_loop().time()
        self._cache[key] = {
            "value": value,
            "expires": now + ttl,
        }

    def _invalidate_cache(self, key: str | None = None) -> None:
        """Инвалидирует кеш."""
        if key is None:
            self._cache.clear()
        elif key in self._cache:
            del self._cache[key]

    async def health_check(self) -> dict[str, Any]:
        """Возвращает состояние здоровья сервиса.

        Returns:
            dict с полями:
            - db_available: bool
            - error_count: int
            - last_error: str | None
            - cache_size: int
        """
        return {
            "service": self.__class__.__name__,
            "db_available": self._db_available,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "cache_size": len(self._cache),
        }
