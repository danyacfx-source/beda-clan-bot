"""Мониторинг здоровья сервисов."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.health")


class HealthStatus(Enum):
    """Статус здоровья сервиса."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ServiceHealth:
    """Здоровье отдельного сервиса."""

    name: str
    status: HealthStatus
    latency_ms: float
    error: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "details": self.details,
        }


class HealthChecker:
    """Проверка здоровья всех сервисов бота."""

    def __init__(self, bot: ClanBot) -> None:
        self.bot = bot
        self._last_check: dict[str, ServiceHealth] = {}

    async def check_all(self) -> dict[str, ServiceHealth]:
        """Проверяет здоровье всех сервисов."""
        results = {}

        # Core services
        results["discord"] = await self._check_discord()
        results["database"] = await self._check_database()
        results["voice"] = await self._check_voice()
        results["memory"] = await self._check_memory()

        # Infrastructure
        results["rate_limiter"] = await self._check_rate_limiter()
        results["circuit_breakers"] = await self._check_circuit_breakers()

        self._last_check = results
        return results

    async def _check_discord(self) -> ServiceHealth:
        """Проверяет подключение к Discord."""
        start = time.monotonic()

        try:
            # Check if bot is connected
            if not self.bot.is_ready():
                return ServiceHealth(
                    name="discord",
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=0,
                    error="Bot not ready",
                )

            # Check gateway latency
            latency = self.bot.latency * 1000  # Convert to ms

            status = HealthStatus.HEALTHY
            if latency > 5000:
                status = HealthStatus.UNHEALTHY
            elif latency > 1000:
                status = HealthStatus.DEGRADED

            elapsed = (time.monotonic() - start) * 1000

            return ServiceHealth(
                name="discord",
                status=status,
                latency_ms=elapsed,
                details={
                    "gateway_latency_ms": round(latency, 2),
                    "guilds": len(self.bot.guilds),
                    "users": len(self.bot.users),
                },
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ServiceHealth(
                name="discord",
                status=HealthStatus.UNHEALTHY,
                latency_ms=elapsed,
                error=str(e),
            )

    async def _check_database(self) -> ServiceHealth:
        """Проверяет подключение к SQLite."""
        start = time.monotonic()

        try:
            db = self.bot.db
            if db is None:
                return ServiceHealth(
                    name="database",
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=0,
                    error="Database not connected",
                )

            # Simple query to check connection
            cursor = await db.conn.execute("SELECT 1")
            await cursor.fetchone()

            # Check integrity
            integrity = await db.integrity_check()

            elapsed = (time.monotonic() - start) * 1000

            status = HealthStatus.HEALTHY
            if integrity != "ok":
                status = HealthStatus.DEGRADED

            return ServiceHealth(
                name="database",
                status=status,
                latency_ms=elapsed,
                details={
                    "integrity": integrity,
                    "path": db.path,
                },
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ServiceHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                latency_ms=elapsed,
                error=str(e),
            )

    async def _check_voice(self) -> ServiceHealth:
        """Проверяет голосовые подключения."""
        active = sum(1 for guild in self.bot.guilds if guild.voice_client and guild.voice_client.is_connected())

        return ServiceHealth(
            name="voice",
            status=HealthStatus.HEALTHY,
            latency_ms=0,
            details={
                "active_connections": active,
            },
        )

    async def _check_memory(self) -> ServiceHealth:
        """Проверяет использование памяти."""
        import tracemalloc

        import psutil

        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024

        status = HealthStatus.HEALTHY
        if memory_mb > 1000:
            status = HealthStatus.UNHEALTHY
        elif memory_mb > 500:
            status = HealthStatus.DEGRADED

        details = {
            "rss_mb": round(memory_mb, 2),
        }

        # Add tracemalloc info if available
        if tracemalloc.is_tracing():
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics("lineno")[:5]
            details["top_allocations"] = [str(stat) for stat in top_stats]

        return ServiceHealth(
            name="memory",
            status=status,
            latency_ms=0,
            details=details,
        )

    async def _check_rate_limiter(self) -> ServiceHealth:
        """Проверяет состояние rate limiter."""
        from app.core.rate_limiter import get_rate_limiter

        stats = get_rate_limiter().get_stats()

        status = HealthStatus.HEALTHY
        if stats.get("retry_after_active"):
            status = HealthStatus.DEGRADED

        return ServiceHealth(
            name="rate_limiter",
            status=status,
            latency_ms=0,
            details=stats,
        )

    async def _check_circuit_breakers(self) -> ServiceHealth:
        """Проверяет состояние circuit breakers."""
        from app.core.circuit_breaker import get_circuit_breaker_registry

        stats = get_circuit_breaker_registry().get_all_stats()

        open_count = sum(1 for s in stats.values() if s["state"] == "open")

        status = HealthStatus.HEALTHY
        if open_count > 0:
            status = HealthStatus.DEGRADED

        return ServiceHealth(
            name="circuit_breakers",
            status=status,
            latency_ms=0,
            details={
                "total": len(stats),
                "open": open_count,
                "services": stats,
            },
        )

    def get_summary(self, results: dict[str, ServiceHealth]) -> dict[str, Any]:
        """Создаёт сводку здоровья."""
        healthy = sum(1 for h in results.values() if h.status == HealthStatus.HEALTHY)
        degraded = sum(1 for h in results.values() if h.status == HealthStatus.DEGRADED)
        unhealthy = sum(1 for h in results.values() if h.status == HealthStatus.UNHEALTHY)

        if unhealthy > 0:
            overall = HealthStatus.UNHEALTHY
        elif degraded > 0:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        return {
            "overall": overall.value,
            "healthy": healthy,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "total": len(results),
            "timestamp": datetime.now(UTC).isoformat(),
            "uptime_seconds": self.bot.uptime.total_seconds(),
        }
