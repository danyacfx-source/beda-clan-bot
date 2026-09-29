"""Rate limiter для Discord API с bucket-based алгоритмом."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("bot.ratelimit")


@dataclass
class RateLimitBucket:
    """Bucket для отслеживания запросов к определённому route."""

    tokens: list[float] = field(default_factory=list)
    last_update: float = 0.0


class DiscordRateLimiter:
    """Глобальный rate limiter для Discord API.

    Использует token bucket алгоритм с поддержкой:
    - Глобальных лимитов (invalid request bucket)
    - Route-specific лимитов
    - Dynamic adjustment на основе заголовков Discord
    """

    # Discord API limits
    GLOBAL_LIMIT = 50  # 50 requests per second globally
    GLOBAL_WINDOW = 1.0

    # Route-specific limits (conservative defaults)
    ROUTE_LIMITS = {
        "messages": (5, 1.0),  # 5 per second
        "message_delete": (5, 1.0),  # 5 per second
        "message_reactions": (5, 1.0),
        "members": (5, 1.0),
        "guilds": (5, 1.0),
        "channels": (5, 1.0),
        "invites": (5, 1.0),
        "default": (10, 1.0),  # Default: 10 per second
    }

    def __init__(self) -> None:
        self._buckets: dict[str, RateLimitBucket] = defaultdict(RateLimitBucket)
        self._global_bucket = RateLimitBucket()
        self._lock = asyncio.Lock()
        self._retry_after_until: float = 0.0

    def _extract_route(self, route: str) -> str:
        """Извлекает тип route из path для определения лимитов."""
        # Normalize route: /channels/123/messages/456 -> messages
        # /guilds/123/members/456 -> members
        parts = route.strip("/").split("/")

        # Extract major parameter categories
        for i, part in enumerate(parts):
            if part in ("channels", "guilds") and i + 2 < len(parts):
                return parts[i + 2]
            elif part == "users" and i + 2 < len(parts):
                return "users"

        return "default"

    async def acquire(self, route: str) -> float:
        """Получает разрешение на выполнение запроса.

        Args:
            route: Discord API route (e.g., "/channels/123/messages")

        Returns:
            Время ожидания в секундах (0 если не было ожидания)

        Raises:
            RuntimeError: Если превышен retry_after
        """
        async with self._lock:
            now = time.monotonic()

            # Check global retry-after
            if now < self._retry_after_until:
                wait_time = self._retry_after_until - now
                logger.warning("Rate limiter: global retry-after for %.2fs", wait_time)
                return wait_time

            # Check global bucket
            global_wait = await self._check_bucket(self._global_bucket, self.GLOBAL_LIMIT, self.GLOBAL_WINDOW, now)

            # Check route-specific bucket
            route_type = self._extract_route(route)
            limit, window = self.ROUTE_LIMITS.get(route_type, self.ROUTE_LIMITS["default"])
            bucket = self._buckets[route_type]
            route_wait = await self._check_bucket(bucket, limit, window, now)

            total_wait = max(global_wait, route_wait)

            if total_wait > 0:
                logger.debug("Rate limiter: waiting %.2fs for route %s", total_wait, route_type)

            return total_wait

    async def _check_bucket(self, bucket: RateLimitBucket, limit: int, window: float, now: float) -> float:
        """Проверяет и обновляет bucket.

        Returns:
            Время ожидания (0 если токен получен)
        """
        # Remove expired tokens
        bucket.tokens = [t for t in bucket.tokens if now - t <= window]

        # Check if we have capacity
        if len(bucket.tokens) < limit:
            bucket.tokens.append(now)
            bucket.last_update = now
            return 0.0

        # Calculate wait time until oldest token expires
        oldest = bucket.tokens[0]
        wait_time = window - (now - oldest)

        if wait_time > 0:
            await asyncio.sleep(wait_time)
            # Retry after waiting
            return await self._check_bucket(bucket, limit, window, time.monotonic())

        # Shouldn't happen, but safety fallback
        bucket.tokens.append(now)
        return 0.0

    def update_from_headers(self, headers: dict[str, str]) -> None:
        """Обновляет лимиты на основе заголовков Discord.

        Discord возвращает:
        - X-RateLimit-Limit: максимальное количество запросов
        - X-RateLimit-Remaining: оставшиеся запросы
        - X-RateLimit-Reset-After: время до сброса (в секундах)
        - Retry-After: время ожидания при 429
        """
        # Handle retry-after
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                wait_seconds = float(retry_after)
                self._retry_after_until = time.monotonic() + wait_seconds
                logger.warning("Rate limiter: Discord returned retry-after %.2fs", wait_seconds)
            except (TypeError, ValueError):
                pass

    async def wait_for_retry_after(self) -> None:
        """Ждёт окончания retry-after если активен."""
        async with self._lock:
            now = time.monotonic()
            if now < self._retry_after_until:
                wait_time = self._retry_after_until - now
                logger.info("Waiting for retry-after: %.2fs", wait_time)
                await asyncio.sleep(wait_time)

    def get_stats(self) -> dict[str, int | float]:
        """Возвращает статистику rate limiter."""
        now = time.monotonic()

        return {
            "global_tokens": len([t for t in self._global_bucket.tokens if now - t <= self.GLOBAL_WINDOW]),
            "active_buckets": len([b for b in self._buckets.values() if any(now - t <= 1.0 for t in b.tokens)]),
            "retry_after_active": now < self._retry_after_until,
        }


# Global instance
_rate_limiter: DiscordRateLimiter | None = None


def get_rate_limiter() -> DiscordRateLimiter:
    """Возвращает глобальный rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = DiscordRateLimiter()
    return _rate_limiter
