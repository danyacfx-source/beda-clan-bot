"""Регрессы ответов веб-панели.

Накрывают баг, из-за которого панель показывала бота оффлайн при живой связи:
`_api_overview` завершался без `return`, отдавал `None`, и middleware падал с
"'NoneType' object has no attribute 'headers'" -> 500 -> фронтенд без `bot_online`.
Плюс пропавший `_record_metrics`, ронявший `/api/monitor` и `/api/stats`.
"""
import ast
import logging
from collections import deque
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web

from app.core.webpanel.webpanel import WebPanel

WEBPANEL_PY = Path(__file__).parent.parent / "app" / "core" / "webpanel" / "webpanel.py"


def _handlers() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(WEBPANEL_PY.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("_api_")
    }


def _request(path: str = "/api/test") -> SimpleNamespace:
    return SimpleNamespace(method="GET", path=path, headers={}, scheme="http", remote="10.0.0.1")


def _panel(ready: bool = True, guild=None) -> WebPanel:
    panel = WebPanel.__new__(WebPanel)
    panel.bot = SimpleNamespace(
        is_ready=lambda: ready,
        user=SimpleNamespace(name="BEDA") if ready else None,
        uptime=timedelta(hours=1, minutes=2),
        latency=0.05,
        guilds=[],
        get_guild=lambda gid: None,
    )
    panel._lat = deque(maxlen=90)
    panel._mem = deque(maxlen=90)
    panel._online = deque(maxlen=90)
    panel._msg_by_hour = deque(maxlen=24)
    panel._msg_by_day = deque(maxlen=30)
    panel._msg_total = 0
    panel._primary_guild = lambda: guild
    return panel


class TestOverviewReturnsResponse:
    async def test_returns_response_when_online(self):
        response = await _panel()._api_overview(_request("/api/overview"))
        assert isinstance(response, web.Response)
        assert response.status == 200

    async def test_payload_carries_online_flag(self):
        response = await _panel()._api_overview(_request("/api/overview"))
        body = response.text
        assert '"ok": true' in body.lower()
        assert '"bot_online": true' in body.lower()

    async def test_when_bot_offline(self):
        response = await _panel(ready=False)._api_overview(_request("/api/overview"))
        assert isinstance(response, web.Response)
        body = response.text
        assert '"bot_online": false' in body.lower()

    async def test_populates_graph_buffers(self):
        panel = _panel()
        await panel._api_overview(_request("/api/overview"))
        assert len(panel._lat) == 1
        assert len(panel._mem) == 1


class TestRecordMetrics:
    def test_records_latency_and_mem(self):
        panel = _panel()
        panel._record_metrics({"latency_ms": 42, "mem_mb": 128.5})
        assert panel._lat[-1]["v"] == 42
        assert panel._mem[-1]["v"] == 128.5
        assert len(panel._online) == 0

    def test_records_nested_guild_online(self):
        panel = _panel()
        panel._record_metrics({"latency_ms": 1, "mem_mb": 2, "guild": {"online": 17}})
        assert panel._online[-1]["v"] == 17

    def test_missing_guild_is_tolerated(self):
        panel = _panel()
        panel._record_metrics({"latency_ms": 5, "mem_mb": 6})
        assert len(panel._online) == 0


class TestHandlersNeverFallThrough:
    """AST-guard: ни один обработчик /api/* не должен терять ответ."""

    def test_panel_has_handlers(self):
        assert len(_handlers()) > 40

    @pytest.mark.parametrize("name", sorted(_handlers()))
    def test_handler_returns_value(self, name: str):
        handler = _handlers()[name]
        returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return) and n.value is not None]
        assert returns, f"{name}() не возвращает ответ — /api/* уйдёт в 500"


class TestSecurityMiddlewareNoneGuard:
    async def test_none_response_becomes_500_with_clear_log(self, caplog):
        async def bad_handler(request):
            return None

        with caplog.at_level(logging.ERROR, logger="bot.webpanel"):
            response = await _panel()._security_middleware(_request("/api/broken"), bad_handler)

        assert response.status == 500
        assert '"ok": false' in response.text.lower()
        assert "не вернул ответ" in caplog.text

    async def test_real_response_gets_security_headers(self):
        async def ok_handler(request):
            return web.json_response({"ok": True})

        response = await _panel()._security_middleware(_request("/api/ok"), ok_handler)
        assert response.status == 200
        assert response.headers["X-Content-Type-Options"] == "nosniff"
