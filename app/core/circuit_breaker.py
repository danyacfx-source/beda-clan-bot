"""Circuit breaker для внешних API."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger("bot.circuit")


class CircuitState(Enum):
    """Состояния circuit breaker."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject all calls
    HALF_OPEN = "half_open"  # Testing if recovered


class CircuitOpenError(Exception):
    """Circuit breaker открыт, запросы отклоняются."""

    pass


@dataclass
class CircuitStats:
    """Статистика circuit breaker."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: float = 0.0
    last_error: str = ""
    state_changes: int = 0


class CircuitBreaker:
    """Circuit breaker для защиты от каскадных сбоев.

    Реализует паттерн Circuit Breaker:
    - CLOSED: Нормальная работа, запросы проходят
    - OPEN: Сбои, все запросы отклоняются
    - HALF_OPEN: Проверка восстановления (один тестовый запрос)

    Args:
        name: Имя сервиса для логирования
        failure_threshold: Количество сбоев для открытия (default: 5)
        recovery_timeout: Время до попытки восстановления в секундах (default: 60)
        half_open_max_calls: Максимум тестовых запросов в HALF_OPEN (default: 3)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
    ) -> None:
        self.name = name
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_timeout = max(0.01, recovery_timeout)
        self._half_open_max_calls = max(1, half_open_max_calls)

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls = 0
        self._stats = CircuitStats()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Текущее состояние circuit breaker."""
        return self._state

    @property
    def stats(self) -> CircuitStats:
        """Статистика circuit breaker."""
        return self._stats

    @property
    def is_open(self) -> bool:
        """Открыт ли circuit breaker."""
        return self._state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        """Закрыт ли circuit breaker (нормальная работа)."""
        return self._state == CircuitState.CLOSED

    async def call(
        self,
        coro: Any,
        fallback: Any = None,
        *,
        is_failure: Callable[[BaseException], bool] | None = None,
    ) -> Any:
        """Выполняет запрос через circuit breaker.

        Args:
            coro: Coroutine для выполнения
            fallback: Значение для возврата при OPEN (optional)
            is_failure: Если задан — используется для классификации результата.
                Default: любое исключение считается сбоем.

        Returns:
            Результат coro или fallback

        Raises:
            CircuitOpenError: Если circuit открыт и нет fallback
            Exception: Оригинальная ошибка при повторных сбоях
        """
        async with self._lock:
            self._stats.total_calls += 1

            # Check if we should transition from OPEN to HALF_OPEN
            if self._state == CircuitState.OPEN:
                if self._should_attempt_recovery():
                    self._transition_to(CircuitState.HALF_OPEN)
                else:
                    self._stats.rejected_calls += 1
                    if fallback is not None:
                        logger.debug("%s: circuit OPEN, returning fallback", self.name)
                        return fallback
                    raise CircuitOpenError(f"{self.name}: circuit breaker is open")

            # In HALF_OPEN, limit concurrent calls
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self._half_open_max_calls:
                    self._stats.rejected_calls += 1
                    if fallback is not None:
                        return fallback
                    raise CircuitOpenError(f"{self.name}: circuit breaker is half-open, max test calls reached")
                self._half_open_calls += 1

        # Execute the call
        try:
            result = await coro
            await self._on_success()
            return result
        except Exception as e:
            if is_failure is not None and not is_failure(e):
                # Business-ошибка (404 канала нет, 401 токен протух и т.п.) —
                # HTTP-обмен состоялся, сервис отвечает. Не разрываем circuit,
                # но засчитываем успешный обмен и пробрасываем ошибку наверх.
                await self._on_success()
                raise
            await self._on_failure(e)
            raise

    def _should_attempt_recovery(self) -> bool:
        """Проверяет, пора ли пробовать восстановление."""
        now = time.monotonic()
        return (now - self._last_failure_time) >= self._recovery_timeout

    async def _on_success(self) -> None:
        """Обрабатывает успешный вызов."""
        async with self._lock:
            self._stats.successful_calls += 1

            if self._state == CircuitState.HALF_OPEN:
                # Recovery successful, close the circuit
                self._transition_to(CircuitState.CLOSED)
                self._half_open_calls = 0
                logger.info("%s: circuit CLOSED (recovered)", self.name)
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        """Обрабатывает неудачный вызов."""
        async with self._lock:
            self._stats.failed_calls += 1
            self._last_failure_time = time.monotonic()
            self._stats.last_failure_time = self._last_failure_time
            self._stats.last_error = str(error)[:500]

            if self._state == CircuitState.HALF_OPEN:
                # Failed during recovery, back to OPEN
                self._transition_to(CircuitState.OPEN)
                self._half_open_calls = 0
                logger.warning("%s: circuit OPEN (recovery failed): %s", self.name, error)
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1

                if self._failure_count >= self._failure_threshold:
                    self._transition_to(CircuitState.OPEN)
                    logger.warning("%s: circuit OPEN (threshold reached: %d failures)", self.name, self._failure_count)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Переходит в новое состояние."""
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self._stats.state_changes += 1

            if new_state == CircuitState.CLOSED:
                self._failure_count = 0

            logger.info("%s: circuit %s -> %s", self.name, old_state.value, new_state.value)

    async def reset(self) -> None:
        """Принудительно сбрасывает circuit breaker."""
        async with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._half_open_calls = 0
            logger.info("%s: circuit manually reset", self.name)


class CircuitBreakerRegistry:
    """Реестр circuit breakers для всех внешних сервисов."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    def get(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> CircuitBreaker:
        """Получает или создаёт circuit breaker для сервиса."""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
        return self._breakers[name]

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Возвращает статистику всех circuit breakers."""
        return {
            name: {
                "state": breaker.state.value,
                "total_calls": breaker.stats.total_calls,
                "successful_calls": breaker.stats.successful_calls,
                "failed_calls": breaker.stats.failed_calls,
                "rejected_calls": breaker.stats.rejected_calls,
                "last_error": breaker.stats.last_error[:200] if breaker.stats.last_error else None,
            }
            for name, breaker in self._breakers.items()
        }

    async def reset_all(self) -> None:
        """Сбрасывает все circuit breakers."""
        for breaker in self._breakers.values():
            await breaker.reset()


# Global registry
_registry: CircuitBreakerRegistry | None = None


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """Возвращает глобальный реестр circuit breakers."""
    global _registry
    if _registry is None:
        _registry = CircuitBreakerRegistry()
    return _registry


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    """Получает circuit breaker для сервиса."""
    return get_circuit_breaker_registry().get(
        name,
        failure_threshold,
        recovery_timeout,
    )
