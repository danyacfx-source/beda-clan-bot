"""Общий надёжный HTTP-клиент для внешних API.

Все внешние интеграции используют одинаковые правила:
- короткие connect/read таймауты;
- повтор только для временных ошибок и 429;
- уважение Retry-After;
- ограничение времени ожидания;
- единый User-Agent и понятная ошибка с HTTP-статусом.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from weakref import WeakSet

import aiohttp
from multidict import CIMultiDictProxy

logger = logging.getLogger("bot.api")

_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class ApiRequestError(RuntimeError):
    """Ошибка HTTP-запроса с сохранёнными статусом и телом ответа."""

    def __init__(self, service: str, status: int, body: Any = None) -> None:
        self.service = service
        self.status = status
        self.body = body
        detail = body if isinstance(body, str) else ""
        super().__init__(f"{service}: HTTP {status}" + (f" — {detail[:240]}" if detail else ""))


class CircuitOpenError(ApiRequestError):
    """Запрос временно остановлен после серии ошибок внешнего API."""

    def __init__(self, service: str, retry_after: float) -> None:
        self.retry_after = max(0.0, retry_after)
        super().__init__(service, 598, f"circuit open; retry after {self.retry_after:.1f}s")


class ApiClient:
    """Минимальный retry-aware клиент поверх aiohttp."""

    _instances: WeakSet[ApiClient] = WeakSet()

    def __init__(
        self,
        service: str,
        *,
        timeout: float = 20.0,
        user_agent: str = "DiscordMegaBot/3.3 (+https://discord.com)",
        proxy: str | None = None,
        max_concurrency: int = 8,
        circuit_failure_threshold: int = 5,
        circuit_reset_seconds: float = 60.0,
    ) -> None:
        self.service = service
        self.timeout = aiohttp.ClientTimeout(total=timeout, connect=min(5.0, timeout), sock_read=timeout)
        self.user_agent = user_agent
        self.proxy = proxy
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(max(1, min(max_concurrency, 64)))
        self._circuit_failure_threshold = max(1, circuit_failure_threshold)
        self._circuit_reset_seconds = max(5.0, circuit_reset_seconds)
        self._failure_streak = 0
        self._circuit_open_until = 0.0
        self._requests = 0
        self._successes = 0
        self._retries = 0
        self._failures = 0
        self._statuses: Counter[str] = Counter()
        self._instances.add(self)

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def json(
        self,
        method: str,
        url: str,
        *,
        attempts: int = 3,
        acceptable: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> tuple[int, Any, CIMultiDictProxy[str]]:
        """Возвращает `(status, json, headers)` или выбрасывает ApiRequestError."""
        attempts = max(1, min(attempts, 5))
        self._before_request()
        request_kwargs = dict(kwargs)
        if self.proxy and "proxy" not in request_kwargs:
            request_kwargs["proxy"] = self.proxy
        async with self._semaphore:
            self._requests += 1
            for attempt in range(attempts):
                try:
                    async with self.session.request(method, url, **request_kwargs) as response:
                        try:
                            body = await response.json(content_type=None)
                        except (aiohttp.ContentTypeError, ValueError):
                            body = None
                        self._statuses[str(response.status)] += 1
                        if response.status in acceptable:
                            self._record_success()
                            return response.status, body, response.headers
                        error = ApiRequestError(self.service, response.status, body)
                        if response.status not in _RETRY_STATUSES or attempt == attempts - 1:
                            self._record_failure(response.status)
                            raise error
                        self._retries += 1
                        await self._backoff(response.headers, attempt)
                except ApiRequestError:
                    raise
                except (aiohttp.ClientError, TimeoutError) as exc:
                    if attempt == attempts - 1:
                        self._record_failure(599)
                        raise ApiRequestError(self.service, 599, str(exc)) from exc
                    self._retries += 1
                    await self._backoff({}, attempt)
        raise ApiRequestError(self.service, 599, "исчерпаны попытки")

    async def text(
        self,
        method: str,
        url: str,
        *,
        attempts: int = 3,
        acceptable: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> tuple[int, str, CIMultiDictProxy[str]]:
        attempts = max(1, min(attempts, 5))
        self._before_request()
        request_kwargs = dict(kwargs)
        if self.proxy and "proxy" not in request_kwargs:
            request_kwargs["proxy"] = self.proxy
        async with self._semaphore:
            self._requests += 1
            for attempt in range(attempts):
                try:
                    async with self.session.request(method, url, **request_kwargs) as response:
                        body = await response.text()
                        self._statuses[str(response.status)] += 1
                        if response.status in acceptable:
                            self._record_success()
                            return response.status, body, response.headers
                        error = ApiRequestError(self.service, response.status, body)
                        if response.status not in _RETRY_STATUSES or attempt == attempts - 1:
                            self._record_failure(response.status)
                            raise error
                        self._retries += 1
                        await self._backoff(response.headers, attempt)
                except ApiRequestError:
                    raise
                except (aiohttp.ClientError, TimeoutError) as exc:
                    if attempt == attempts - 1:
                        self._record_failure(599)
                        raise ApiRequestError(self.service, 599, str(exc)) from exc
                    self._retries += 1
                    await self._backoff({}, attempt)
        raise ApiRequestError(self.service, 599, "исчерпаны попытки")

    def _before_request(self) -> None:
        remaining = self._circuit_open_until - time.monotonic()
        if remaining > 0:
            raise CircuitOpenError(self.service, remaining)
        if self._circuit_open_until:
            self._circuit_open_until = 0.0

    def _record_success(self) -> None:
        self._successes += 1
        self._failure_streak = 0

    def _record_failure(self, status: int) -> None:
        if status not in _RETRY_STATUSES and status != 599:
            return
        self._failures += 1
        self._failure_streak += 1
        if self._failure_streak >= self._circuit_failure_threshold:
            self._circuit_open_until = time.monotonic() + self._circuit_reset_seconds
            logger.error(
                "%s: circuit breaker открыт на %.0fс после %d ошибок",
                self.service,
                self._circuit_reset_seconds,
                self._failure_streak,
            )

    def snapshot(self) -> dict[str, Any]:
        remaining = max(0.0, self._circuit_open_until - time.monotonic())
        return {
            "service": self.service,
            "requests": self._requests,
            "successes": self._successes,
            "retries": self._retries,
            "failures": self._failures,
            "failure_streak": self._failure_streak,
            "circuit_open": remaining > 0,
            "circuit_retry_after": round(remaining, 2),
            "statuses": dict(self._statuses),
        }

    @classmethod
    def snapshots(cls) -> list[dict[str, Any]]:
        return [client.snapshot() for client in cls._instances]

    async def _backoff(self, headers: Any, attempt: int) -> None:
        retry_after = _retry_after_seconds(headers)
        delay = retry_after if retry_after is not None else min(8.0, 2**attempt)
        delay += random.uniform(0.0, 0.25)
        logger.warning("%s: временная ошибка API, повтор через %.2fс", self.service, delay)
        await asyncio.sleep(delay)


def _retry_after_seconds(headers: Any) -> float | None:
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return max(0.0, min(30.0, float(raw)))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            return max(0.0, min(30.0, (target - datetime.now(UTC)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None
