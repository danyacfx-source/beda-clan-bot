"""Тесты общего retry-aware HTTP-клиента."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core.api_client import ApiClient, ApiRequestError, CircuitOpenError


async def test_api_client_retries_temporary_http_error() -> None:
    calls = 0

    async def handler(_request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return web.json_response({"error": "busy"}, status=503)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server()
    client = ApiClient("test", timeout=2)
    client._backoff = AsyncMock()
    try:
        status, body, _ = await client.json("GET", server.make_url("/"), attempts=2)
        assert status == 200
        assert body == {"ok": True}
        assert calls == 2
        client._backoff.assert_awaited_once()
    finally:
        await client.close()
        await server.close()


async def test_api_client_opens_circuit_after_transient_failures() -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response({"error": "down"}, status=503)

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server()
    client = ApiClient("test", timeout=2, circuit_failure_threshold=2, circuit_reset_seconds=30)
    try:
        for _ in range(2):
            try:
                await client.json("GET", server.make_url("/"), attempts=1)
            except ApiRequestError as exc:
                assert exc.status == 503
        try:
            await client.json("GET", server.make_url("/"), attempts=1)
        except CircuitOpenError as exc:
            assert exc.status == 598
        else:
            raise AssertionError("Circuit breaker не открылся")
        snapshot = client.snapshot()
        assert snapshot["circuit_open"] is True
        assert snapshot["failures"] == 2
    finally:
        await client.close()
        await server.close()


async def test_api_client_exposes_non_retryable_error() -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response({"error": "not found"}, status=404)

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server()
    client = ApiClient("test", timeout=2)
    try:
        try:
            await client.json("GET", server.make_url("/"))
        except ApiRequestError as exc:
            assert exc.status == 404
        else:
            raise AssertionError("ApiRequestError не был выброшен")
    finally:
        await client.close()
        await server.close()


async def test_api_client_passes_proxy_per_request_not_to_session() -> None:
    client = ApiClient("test", proxy="http://127.0.0.1:8080")
    with patch.object(client.session, "request", new_callable=MagicMock) as request:
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.status = 200
        response.headers = {}
        response.json.return_value = {"ok": True}
        request.return_value = response
        status, body, _ = await client.json("GET", "https://example.test")
        assert status == 200
        assert body == {"ok": True}
        assert request.call_args.kwargs["proxy"] == "http://127.0.0.1:8080"
    await client.close()
