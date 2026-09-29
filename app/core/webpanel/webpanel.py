"""Вебпанель конструктора эмбедов: браузерный редактор, отправка через бота и прокси вебхуков."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import secrets
import time
import tracemalloc
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import urlencode, urlparse

import aiohttp
import discord
import psutil
from aiohttp import web
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.core import embeds
from app.core.api_client import ApiClient
from app.core.ticket_content import (
    CLAN_INTRO_FOOTER,
    CLAN_INTRO_TEXT,
    CLAN_INTRO_TITLE,
    CLAN_PANEL_BUTTON,
    CLAN_PANEL_EMOJI,
    CLAN_PANEL_FOOTER,
    CLAN_PANEL_TEXT,
    CLAN_PANEL_TITLE,
)
from app.core.webpanel.log_ring import RingBufferHandler
from app.core.webpanel.routes import register_routes

if TYPE_CHECKING:
    from app.core.bot import ClanBot
    from app.services import Services
    from app.types import GiveawayRow

logger = logging.getLogger("bot.webpanel")


class _PanelSession(TypedDict):
    expires: float
    role: str
    csrf: str


_PANEL_ROLE_KEY = web.RequestKey("panel_role", str)

_INDEX_PATH = Path(__file__).parent / "index.html"
_SCRIPT_PATH = Path(__file__).parent / "panel.js"
_LOGS_PAGE_PATH = Path(__file__).parent / "logs.html"
_AUDIT_PAGE_PATH = Path(__file__).parent / "audit.html"
_WEBHOOK_RE = re.compile(r"^https://(?:discord\.com|discordapp\.com)/api/webhooks/(\d+)/([A-Za-z0-9_\-]+)$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
_MAX_EMBEDS = 10
_LOGIN_WINDOW = 60.0
_LOGIN_LIMIT = 5
_SESSION_TTL = 24 * 3600
_SESSION_MAX = 2000
_RATE_LIMIT_MAX = 600
_RATE_LIMIT_WINDOW = 60.0
_TOKEN_FILE = ".panel-token"
_UPLOAD_DIRNAME = "uploads"
_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_MAX_REQUEST_BYTES = _MAX_UPLOAD_BYTES + 256 * 1024
_UPLOAD_NAME_RE = re.compile(r"^[0-9a-fA-F]{32}\.(?:png|jpg|jpeg|gif|webp)$")
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
}
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https: http:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
_LOG_RING_SIZE = 2000
_MAX_COMPONENT_ROWS = 5
_MAX_COMPONENT_PER_ROW = 5
_MAX_BUTTON_LABEL = 80
_BUTTON_STYLES = {1, 2, 3, 4, 5}
_BUTTON_STYLE_BY_INT = {
    1: discord.ButtonStyle.primary,
    2: discord.ButtonStyle.secondary,
    3: discord.ButtonStyle.success,
    4: discord.ButtonStyle.danger,
    5: discord.ButtonStyle.link,
}
_SETTING_COLUMNS = (
    "log_channel_id",
    "ticket_category_id",
    "member_log_channel_id",
    "message_log_channel_id",
    "voice_log_channel_id",
    "mod_log_channel_id",
    "bot_log_channel_id",
)

_TICKET_TEXT_FIELDS = (
    "ticket_panel_title",
    "ticket_panel_description",
    "ticket_panel_footer",
    "ticket_open_label",
    "ticket_open_emoji",
    "ticket_intro_title",
    "ticket_intro_description",
    "ticket_intro_footer",
    "ticket_close_label",
    "ticket_close_emoji",
    "ticket_channel_prefix",
)

_TICKET_DEFAULTS = {
    "ticket_panel_title": CLAN_PANEL_TITLE,
    "ticket_panel_description": CLAN_PANEL_TEXT,
    "ticket_panel_footer": CLAN_PANEL_FOOTER,
    "ticket_open_label": CLAN_PANEL_BUTTON,
    "ticket_open_emoji": CLAN_PANEL_EMOJI,
    "ticket_intro_title": CLAN_INTRO_TITLE,
    "ticket_intro_description": CLAN_INTRO_TEXT,
    "ticket_intro_footer": CLAN_INTRO_FOOTER,
    "ticket_close_label": "Закрыть тикет",
    "ticket_close_emoji": "🔒",
    "ticket_channel_prefix": "ticket",
}


def _trim_embeds(raw: Any) -> list[dict[str, Any]]:
    """Обрезает эмбеды до лимитов Discord для отправки (webhook-режим)."""
    trimmed: list[dict[str, Any]] = []
    for item in (raw or [])[:_MAX_EMBEDS]:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        if item.get("title"):
            entry["title"] = str(item["title"])[:256]
        if item.get("description"):
            entry["description"] = str(item["description"])[:4000]
        color = item.get("color")
        if color is not None:
            try:
                entry["color"] = int(color) & 0xFFFFFF
            except (TypeError, ValueError):
                pass
        author = item.get("author")
        if isinstance(author, dict) and author.get("name"):
            author_entry: dict[str, Any] = {"name": str(author["name"])[:256]}
            if author.get("icon_url"):
                author_entry["icon_url"] = str(author["icon_url"])[:2048]
            if author.get("url"):
                author_entry["url"] = str(author["url"])[:2048]
            entry["author"] = author_entry
        footer = item.get("footer")
        if isinstance(footer, dict) and footer.get("text"):
            footer_entry: dict[str, Any] = {"text": str(footer["text"])[:2048]}
            if footer.get("icon_url"):
                footer_entry["icon_url"] = str(footer["icon_url"])[:2048]
            entry["footer"] = footer_entry
        if item.get("thumbnail"):
            entry["thumbnail"] = {"url": str(item["thumbnail"].get("url", ""))[:2048]}
        if item.get("image"):
            entry["image"] = {"url": str(item["image"].get("url", ""))[:2048]}
        timestamp = item.get("timestamp")
        if timestamp:
            entry["timestamp"] = str(timestamp)[:64]
        fields = []
        for field in (item.get("fields") or [])[:25]:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            fields.append(
                {
                    "name": str(field["name"])[:256],
                    "value": str(field.get("value") or "")[:1024],
                    "inline": bool(field.get("inline", False)),
                }
            )
        if fields:
            entry["fields"] = fields
        if entry:
            trimmed.append(entry)
    return trimmed


def _trim_components(raw: Any) -> list[dict[str, Any]]:
    """Нормализует кнопки (components) до формата Discord webhook. Возвращает список ActionRow."""
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for row in (raw or [])[:_MAX_COMPONENT_ROWS]:
        if not isinstance(row, list):
            continue
        buttons: list[dict[str, Any]] = []
        for item in (row or [])[:_MAX_COMPONENT_PER_ROW]:
            if not isinstance(item, dict):
                continue
            try:
                style = int(item.get("style") or 1)
            except (TypeError, ValueError):
                style = 1
            if style not in _BUTTON_STYLES:
                style = 1
            label = str(item.get("label") or "")[:_MAX_BUTTON_LABEL]
            if not label:
                continue
            url = str(item.get("url") or "")[:2048]
            if style == 5:
                if not url.startswith(("https://", "http://")):
                    continue
            else:
                url = ""
            buttons.append({"type": 2, "label": label, "style": style, "url": url})
        if buttons:
            rows.append({"type": 1, "components": buttons})
    return rows


def _view_from_components(raw: Any) -> discord.ui.View | None:
    """Собирает View для отправки через бота. ``None`` — кнопки не трогаем."""
    if raw is None:
        return None
    view = discord.ui.View()
    for row in _trim_components(raw):
        for component in row.get("components", [])[:_MAX_COMPONENT_PER_ROW]:
            style = _BUTTON_STYLE_BY_INT.get(component["style"], discord.ButtonStyle.secondary)
            view.add_item(discord.ui.Button(label=component["label"], style=style, url=component.get("url") or None))
    return view


def _components_from_message(message: discord.Message) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for message_view in getattr(message, "components", []) or []:
        row: list[dict[str, Any]] = []
        for component in getattr(message_view, "children", []) or []:
            if isinstance(component, discord.Button):
                row.append(
                    {
                        "label": component.label or "",
                        "style": str(component.style.value if component.style else 1),
                        "url": str(getattr(component, "url", "") or ""),
                    }
                )
        if row:
            rows.append(row)
    return rows


def _components_from_raw(raw: Any) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for action in (raw or [])[:_MAX_COMPONENT_ROWS]:
        if not isinstance(action, dict):
            continue
        row: list[dict[str, Any]] = []
        for component in (action.get("components") or [])[:_MAX_COMPONENT_PER_ROW]:
            if not isinstance(component, dict) or component.get("type") != 2:
                continue
            row.append(
                {
                    "label": str(component.get("label") or ""),
                    "style": str(component.get("style") or 1),
                    "url": str(component.get("url") or ""),
                }
            )
        if row:
            rows.append(row)
    return rows


def _embed_from_dict(data: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed()
    if data.get("title"):
        embed.title = str(data["title"])[:256]
    if data.get("description"):
        embed.description = str(data["description"])[:4000]
    color = data.get("color")
    if color is not None:
        try:
            embed.color = int(color) & 0xFFFFFF
        except (TypeError, ValueError):
            pass
    author = data.get("author")
    if isinstance(author, dict) and author.get("name"):
        embed.set_author(
            name=str(author["name"])[:256],
            url=str(author.get("url") or "")[:2048] or None,
            icon_url=str(author.get("icon_url") or "")[:2048] or None,
        )
    footer = data.get("footer")
    if isinstance(footer, dict) and footer.get("text"):
        embed.set_footer(
            text=str(footer["text"])[:2048],
            icon_url=str(footer.get("icon_url") or "")[:2048] or None,
        )
    if data.get("thumbnail"):
        embed.set_thumbnail(url=str(data["thumbnail"].get("url", ""))[:2048])
    if data.get("image"):
        embed.set_image(url=str(data["image"].get("url", ""))[:2048])
    timestamp = data.get("timestamp")
    if timestamp:
        try:
            embed.timestamp = datetime.fromisoformat(str(timestamp)[:64].replace("Z", "+00:00"))
        except ValueError:
            embed.timestamp = datetime.now(UTC)
    for field in (data.get("fields") or [])[:25]:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "")[:256]
        if not name:
            continue
        embed.add_field(name=name, value=str(field.get("value") or "")[:1024], inline=bool(field.get("inline", False)))
    return embed


def _embed_to_client(embed: discord.Embed) -> dict[str, Any]:
    author = {"name": embed.author.name, "icon_url": embed.author.icon_url, "url": embed.author.url} if embed.author.name else {}
    footer = {"text": embed.footer.text, "icon_url": embed.footer.icon_url} if embed.footer.text else {}
    return {
        "title": embed.title or "",
        "description": embed.description or "",
        "color": embed.color.value if getattr(embed.color, "value", None) is not None else None,
        "author": author,
        "footer": footer,
        "thumbnail": {"url": embed.thumbnail.url} if embed.thumbnail else {},
        "image": {"url": embed.image.url} if embed.image else {},
        "timestamp": embed.timestamp.isoformat() if embed.timestamp else None,
        "fields": [{"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields[:25]],
    }


def _message_to_client(message: discord.Message) -> dict[str, Any]:
    return {
        "content": message.content or "",
        "embeds": [_embed_to_client(embed) for embed in message.embeds[:_MAX_EMBEDS]],
        "components": _components_from_message(message),
    }


def _raw_to_client(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": raw.get("content") or "",
        "embeds": [_raw_embed_to_client(embed) for embed in (raw.get("embeds") or [])[:_MAX_EMBEDS]],
        "components": _components_from_raw(raw.get("components")),
    }


def _raw_embed_to_client(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": entry.get("title") or "",
        "description": entry.get("description") or "",
        "color": entry.get("color"),
        "author": entry.get("author") or {},
        "footer": entry.get("footer") or {},
        "thumbnail": entry.get("thumbnail") or {},
        "image": entry.get("image") or {},
        "timestamp": entry.get("timestamp"),
        "fields": [
            {"name": field.get("name") or "", "value": field.get("value") or "", "inline": bool(field.get("inline"))}
            for field in (entry.get("fields") or [])[:25]
        ],
    }


class WebPanel:
    def __init__(self, bot: ClanBot) -> None:
        self.bot = bot
        config = bot.config
        if config.panel_port is None:
            raise RuntimeError("Веб-панель включена без PANEL_PORT")
        self.host = config.panel_host or "127.0.0.1"
        self.port = config.panel_port
        self.password = config.panel_password
        self.public_url = config.panel_public_url
        self._oauth_client_id = config.panel_oauth_client_id
        self._oauth_client_secret = config.panel_oauth_client_secret
        self._oauth_redirect_url = config.panel_oauth_redirect_url
        self._bridge_token = config.panel_bridge_token
        self._uploads_dir = Path(config.db_path).parent / _UPLOAD_DIRNAME
        self._index_html = _INDEX_PATH.read_text(encoding="utf-8")
        self._index_js = _SCRIPT_PATH.read_text(encoding="utf-8")
        self._logs_html = _LOGS_PAGE_PATH.read_text(encoding="utf-8") if _LOGS_PAGE_PATH.exists() else ""
        self._audit_html = _AUDIT_PAGE_PATH.read_text(encoding="utf-8") if _AUDIT_PAGE_PATH.exists() else ""
        self._static_token: str | None = None if self.password else self._load_static_token()
        self._sessions: dict[str, _PanelSession] = {}
        self._oauth_states: dict[str, float] = {}
        self._panel_passwords: dict[str, str] = {}
        self._panel_password_hashes: dict[str, str] = {}
        self._password_hasher = PasswordHasher()
        if self.password:
            self._panel_passwords["owner"] = self.password
        for role in ("admin", "moderator", "viewer"):
            value = getattr(config, f"panel_{role}_password", None)
            if value:
                self._panel_passwords[role] = value
        for role in ("owner", "admin", "moderator", "viewer"):
            value = getattr(config, f"panel_{role}_password_hash", None)
            if value:
                self._panel_password_hashes[role] = value
        self._password_auth = bool(self._panel_passwords or self._panel_password_hashes)
        self._rate_hits: dict[str, deque[float]] = defaultdict(deque)
        self._login_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._runner: web.AppRunner | None = None
        self._http: aiohttp.ClientSession | None = None
        self._ring: RingBufferHandler | None = None
        self._lat: deque[dict[str, Any]] = deque(maxlen=90)
        self._mem: deque[dict[str, Any]] = deque(maxlen=90)
        self._online: deque[dict[str, Any]] = deque(maxlen=90)
        self._msg_by_hour: deque[dict[str, Any]] = deque(maxlen=24 * 7)
        self._msg_by_day: deque[dict[str, Any]] = deque(maxlen=45)
        self._msg_total: int = 0
        self._msg_unknown_logs: int = 0
        self._listener_registered = False
        self._analytics_task: asyncio.Task[None] | None = None
        self._pending_activity: dict[tuple[int, str], int] = defaultdict(int)
        self._analytics_clients: set[web.WebSocketResponse] = set()

    @property
    def services(self) -> Services:
        services = self.bot.services
        if services is None:
            raise RuntimeError("Сервисы недоступны до запуска вебпанели")
        return services

    @property
    def _oauth_enabled(self) -> bool:
        return bool(self._oauth_client_id and self._oauth_client_secret and self._oauth_redirect_url)

    # --- жизненный цикл ---

    def _load_static_token(self) -> str:
        path = Path(self.bot.config.db_path).parent / _TOKEN_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        token = secrets.token_urlsafe(32)
        path.write_text(token, encoding="utf-8")
        try:
            path.chmod(0o600)
        except (OSError, NotImplementedError):
            pass
        return token

    def _create_app(self) -> web.Application:
        # aiohttp defaults to a 1 MiB request limit. That made the advertised
        # 8 MiB image-upload limit unusable for valid uploads.
        app = web.Application(
            middlewares=[self._security_middleware],
            client_max_size=_MAX_REQUEST_BYTES,
        )
        register_routes(self, app)
        return app

    async def start(self) -> None:
        if self._runner is not None:
            return
        # A static token is embedded into the page when no password is set.
        # That is convenient for localhost, but it would expose full admin
        # access to anyone who can open a public URL. Fail closed instead of
        # relying on a warning that is easy to miss in deployment logs.
        if not (self._password_auth or self._oauth_enabled) and (self.host not in _LOCAL_HOSTS or self.public_url):
            raise RuntimeError(
                "PANEL_PASSWORD обязателен для публичной веб-панели (задайте пароль или оставьте PANEL_HOST локальным без PANEL_PUBLIC_URL)"
            )
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=5))
        if not (self._password_auth or self._oauth_enabled) and self.host not in _LOCAL_HOSTS:
            logger.warning(
                "Вебпанель без PANEL_PASSWORD слушает %s:%d — страница доступна по статическому токену. "
                "В открытых сетях задайте PANEL_PASSWORD.",
                self.host,
                self.port,
            )
        runner = web.AppRunner(self._create_app())
        try:
            await runner.setup()
            await web.TCPSite(runner, self.host, self.port).start()
        except Exception:
            await runner.cleanup()
            await self._http.close()
            self._http = None
            raise
        self._runner = runner
        if not self._listener_registered:
            self.bot.add_listener(self._on_message_hook, "on_message")
            self._listener_registered = True
        self._analytics_task = asyncio.create_task(self._analytics_flush_loop())
        self._ring = RingBufferHandler(_LOG_RING_SIZE)
        logging.getLogger().addHandler(self._ring)
        logger.info("Вебпанель запущена: http://%s:%d/admin", self.host, self.port)

    async def stop(self) -> None:
        if self._analytics_task is not None:
            self._analytics_task.cancel()
            try:
                await self._analytics_task
            except asyncio.CancelledError:
                pass
            self._analytics_task = None
        await self._flush_activity()
        for websocket in tuple(self._analytics_clients):
            await websocket.close()
        self._analytics_clients.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._ring is not None:
            logging.getLogger().removeHandler(self._ring)
            self._ring = None
        if getattr(self, "_listener_registered", False):
            self.bot.remove_listener(self._on_message_hook, "on_message")
            self._listener_registered = False
        if self._http is not None:
            await self._http.close()
            self._http = None

    # --- авторизация, origin и безопасность ---

    @web.middleware
    async def _security_middleware(self, request: web.Request, handler: Any) -> web.Response:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, headers=exc.headers, text=exc.text)
        except Exception:
            logger.exception("Необработанная ошибка HTTP %s %s", request.method, request.path)
            response = self._json({"ok": False, "error": "Внутренняя ошибка сервера"}, status=500)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=(), usb=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Content-Security-Policy": _CSP,
            "Cache-Control": "no-store",
        }
        proto = request.headers.get("X-Forwarded-Proto") or request.scheme
        if proto == "https":
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers.update(headers)
        return response

    @staticmethod
    def _role_rank(role: str) -> int:
        return {"viewer": 10, "moderator": 20, "admin": 30, "owner": 40}.get(role, 0)

    def _required_role(self, request: web.Request) -> str:
        if request.method == "GET":
            if request.path in {"/api/backup", "/api/backup/db", "/api/metrics"}:
                return "admin"
            return "viewer"
        if request.path in {
            "/api/settings",
            "/api/automod",
            "/api/tickets/panel",
            "/api/server/members/roles",
        } or request.path.startswith(("/api/upload", "/api/webhook", "/api/bot", "/api/automod/")):
            return "admin"
        return "moderator"

    async def _record_admin_audit(self, request: web.Request, role: str, response: web.Response) -> None:
        if request.method == "GET" or response.status >= 400 or self.bot.db is None:
            return
        try:
            await self.bot.db.record_admin_audit(
                role,
                "panel request",
                request.method,
                request.path,
                request.remote or "",
            )
        except Exception:
            logger.exception("Не удалось записать аудит веб-панели")

    def _authorized(self, handler, required_role: str | None = None):
        async def wrapped(request: web.Request) -> web.Response:
            if not self._rate_ok(request) or not self._allowed_origin(request):
                return self._json({"ok": False, "error": "Unauthorized"}, status=401)
            role = self._check_token(request)
            required = required_role or self._required_role(request)
            if role is None or self._role_rank(role) < self._role_rank(required):
                return self._json({"ok": False, "error": "Недостаточно прав"}, status=403 if role else 401)
            if request.method != "GET" and self._password_auth:
                token = request.headers.get("X-Panel-Token", "")
                expected_csrf = self._csrf_for_token(token)
                provided_csrf = request.headers.get("X-Panel-CSRF", "")
                if not expected_csrf or not provided_csrf or not secrets.compare_digest(expected_csrf, provided_csrf):
                    return self._json({"ok": False, "error": "Некорректный CSRF-токен"}, status=403)
            request[_PANEL_ROLE_KEY] = role
            response = await handler(request)
            await self._record_admin_audit(request, role, response)
            return response

        return wrapped

    def _bridge_or_authorized(self, handler, required_role: str | None = None):
        authorized = self._authorized(handler, required_role)

        async def wrapped(request: web.Request) -> web.Response:
            supplied = request.headers.get("X-Bridge-Token", "")
            if self._bridge_token and supplied and self._rate_ok(request) and secrets.compare_digest(supplied, self._bridge_token):
                return await handler(request)
            return await authorized(request)

        return wrapped

    def _rate_key(self, request: web.Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or (request.remote or "?")
        return request.remote or "?"

    def _rate_ok(self, request: web.Request) -> bool:
        key = self._rate_key(request)
        now = time.time()
        hits = self._rate_hits[key]
        while hits and hits[0] < now - _RATE_LIMIT_WINDOW:
            hits.popleft()
        if len(hits) >= _RATE_LIMIT_MAX:
            return False
        hits.append(now)
        return True

    def _allowed_origin(self, request: web.Request) -> bool:
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if not origin:
            return True
        parsed = urlparse(origin)
        host = parsed.hostname
        if not host:
            return False
        if host == self.host or host in _LOCAL_HOSTS:
            return True
        if host == (request.host or "").split(":")[0]:
            return True
        for entry in (self.public_url or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            candidate = urlparse(entry).hostname if "://" in entry else entry.split("/")[0].split(":")[0]
            if candidate == host:
                return True
        return False

    def _check_token(self, request: web.Request) -> str | None:
        return self._check_token_value(request.headers.get("X-Panel-Token", ""))

    def _csrf_for_token(self, token: str) -> str | None:
        session = self._sessions.get(token)
        if session is None or session["expires"] <= time.time():
            return None
        return session["csrf"]

    def _check_token_value(self, token: str) -> str | None:
        if not token:
            return None
        if not self._password_auth:
            return "owner" if self._static_token and secrets.compare_digest(token, self._static_token) else None
        now = time.time()
        active: dict[str, _PanelSession] = {}
        role: str | None = None
        for saved, session in self._sessions.items():
            if session["expires"] > now:
                active[saved] = session
                if secrets.compare_digest(token, saved):
                    role = session["role"]
        self._sessions = active
        if len(self._sessions) > _SESSION_MAX:
            self._sessions = dict(sorted(self._sessions.items(), key=lambda item: item[1]["expires"])[:_SESSION_MAX])
        return role

    async def _api_logout(self, request: web.Request) -> web.Response:
        token = request.headers.get("X-Panel-Token", "")
        self._sessions.pop(token, None)
        return self._json({"ok": True})

    async def _api_session(self, request: web.Request) -> web.Response:
        role = request.get(_PANEL_ROLE_KEY, "viewer")
        token = request.headers.get("X-Panel-Token", "")
        return self._json(
            {
                "ok": True,
                "role": role,
                "csrf": self._csrf_for_token(token),
                "permissions": {
                    "read": True,
                    "moderate": self._role_rank(role) >= self._role_rank("moderator"),
                    "admin": self._role_rank(role) >= self._role_rank("admin"),
                    "owner": role == "owner",
                },
            }
        )

    async def _oauth_start(self, request: web.Request) -> web.Response:
        if not self._oauth_enabled:
            raise web.HTTPNotFound(text="Discord OAuth2 не настроен")
        now = time.time()
        self._oauth_states = {key: expires for key, expires in self._oauth_states.items() if expires > now}
        state = secrets.token_urlsafe(32)
        self._oauth_states[state] = now + 300
        query = urlencode(
            {
                "client_id": self._oauth_client_id,
                "response_type": "code",
                "redirect_uri": self._oauth_redirect_url,
                "scope": "identify guilds",
                "state": state,
            }
        )
        return web.Response(status=302, headers={"Location": f"https://discord.com/oauth2/authorize?{query}"})

    async def _oauth_callback(self, request: web.Request) -> web.Response:
        state = request.query.get("state", "")
        if not self._oauth_enabled or not state or self._oauth_states.pop(state, 0) <= time.time():
            raise web.HTTPBadRequest(text="OAuth2 state истёк или недействителен")
        if request.query.get("error"):
            raise web.HTTPUnauthorized(text="Discord OAuth2 отклонил авторизацию")
        code = request.query.get("code", "")
        if not code or self._http is None:
            raise web.HTTPBadRequest(text="Discord OAuth2 не вернул code")

        async with self._http.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": self._oauth_client_id,
                "client_secret": self._oauth_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._oauth_redirect_url,
            },
        ) as token_response:
            if token_response.status != 200:
                raise web.HTTPUnauthorized(text="Не удалось обменять OAuth2 code")
            token_data = await token_response.json()
        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise web.HTTPUnauthorized(text="Discord не вернул access token")

        headers = {"Authorization": f"Bearer {access_token}"}
        async with self._http.get("https://discord.com/api/users/@me", headers=headers) as user_response:
            if user_response.status != 200:
                raise web.HTTPUnauthorized(text="Не удалось получить Discord-профиль")
            user_data = await user_response.json()
        try:
            user_id = int(user_data["id"])
        except (KeyError, TypeError, ValueError):
            raise web.HTTPUnauthorized(text="Некорректный Discord-профиль")

        async with self._http.get("https://discord.com/api/users/@me/guilds", headers=headers) as guilds_response:
            if guilds_response.status != 200:
                raise web.HTTPUnauthorized(text="Не удалось получить список серверов")
            guilds = await guilds_response.json()
        if not isinstance(guilds, list):
            raise web.HTTPUnauthorized(text="Discord вернул некорректный список серверов")
        primary_guild = self._primary_guild()
        target_guild = self.bot.config.guild_id or (primary_guild.id if primary_guild else None)
        member_guild = next(
            (item for item in guilds if isinstance(item, dict) and str(item.get("id")) == str(target_guild)),
            None,
        )
        if member_guild is None:
            raise web.HTTPForbidden(text="У Discord-пользователя нет доступа к серверу бота")

        try:
            permissions = int(str(member_guild.get("permissions") or 0))
        except (TypeError, ValueError):
            raise web.HTTPUnauthorized(text="Discord вернул некорректные права пользователя")
        if self.bot.config.owner_id == user_id or bool(member_guild.get("owner")):
            role = "owner"
        elif permissions & (1 << 3) or permissions & (1 << 5):
            role = "admin"
        elif permissions & (1 << 13):
            role = "moderator"
        else:
            role = "viewer"

        session_token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self._sessions[session_token] = {"expires": time.time() + _SESSION_TTL, "role": role, "csrf": csrf}
        if len(self._sessions) > _SESSION_MAX:
            self._sessions = dict(sorted(self._sessions.items(), key=lambda item: item[1]["expires"])[:_SESSION_MAX])
        query = urlencode({"oauth_token": session_token, "oauth_csrf": csrf})
        return web.Response(status=302, headers={"Location": f"/admin?{query}"})

    @staticmethod
    def _json(data: dict[str, Any], status: int = 200) -> web.Response:
        return web.json_response(data, status=status)

    # --- страницы ---

    async def _redirect_index(self, request: web.Request) -> web.Response:
        raise web.HTTPFound("/admin")

    async def _serve_index(self, request: web.Request) -> web.Response:
        html = self._index_html
        if self._password_auth:
            html = html.replace("__PANEL_TOKEN__", "")
        else:
            html = html.replace("__PANEL_TOKEN__", self._static_token or "")
        html = html.replace("__PANEL_OAUTH__", "1" if self._oauth_enabled else "0")
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def _serve_logs_page(self, request: web.Request) -> web.Response:
        html = self._logs_html or "<h1>/logs</h1><p>Файл logs.html не найден.</p>"
        if self._password_auth:
            html = html.replace("__PANEL_TOKEN__", "")
        else:
            html = html.replace("__PANEL_TOKEN__", self._static_token or "")
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def _serve_audit_page(self, request: web.Request) -> web.Response:
        html = self._audit_html or "<h1>/audit</h1><p>Файл audit.html не найден.</p>"
        if self._password_auth:
            html = html.replace("__PANEL_TOKEN__", "")
        else:
            html = html.replace("__PANEL_TOKEN__", self._static_token or "")
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def _serve_script(self, request: web.Request) -> web.Response:
        body = self._index_js
        if self._password_auth:
            body = body.replace("__PANEL_TOKEN__", "").replace("__PANEL_LOGIN__", "1")
        else:
            body = body.replace("__PANEL_TOKEN__", self._static_token or "").replace("__PANEL_LOGIN__", "0")
        body = body.replace("__PANEL_OAUTH__", "1" if self._oauth_enabled else "0")
        return web.Response(text=body, content_type="text/javascript", charset="utf-8")

    # --- API: login / status / channels ---

    async def _api_login(self, request: web.Request) -> web.Response:
        if not self._password_auth:
            return self._json({"ok": False, "error": "Пароль не настроен"}, status=400)
        if not self._allowed_origin(request):
            return self._json({"ok": False, "error": "Unauthorized"}, status=401)
        ip = request.remote or "?"
        now = time.time()
        attempts = self._login_attempts[ip]
        while attempts and attempts[0] < now - _LOGIN_WINDOW:
            attempts.popleft()
        if len(attempts) >= _LOGIN_LIMIT:
            return self._json({"ok": False, "error": "Слишком много попыток. Подождите минуту."}, status=429)
        try:
            payload = await request.json()
        except Exception:
            return self._json({"ok": False, "error": "Некорректный JSON"}, status=400)
        password = str(payload.get("password") or "")
        role = next(
            (candidate for candidate, configured in self._panel_passwords.items() if secrets.compare_digest(password, configured)),
            None,
        )
        if role is None:
            for candidate, configured in self._panel_password_hashes.items():
                try:
                    if self._password_hasher.verify(configured, password):
                        role = candidate
                        break
                except VerificationError:
                    continue
        if role is None:
            attempts.append(now)
            return self._json({"ok": False, "error": "Неверный пароль"}, status=401)
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self._sessions[token] = {"expires": now + _SESSION_TTL, "role": role, "csrf": csrf}
        if len(self._sessions) > _SESSION_MAX:
            self._sessions = dict(sorted(self._sessions.items(), key=lambda item: item[1]["expires"])[:_SESSION_MAX])
        return self._json({"ok": True, "token": token, "role": role, "csrf": csrf})

    async def _api_status(self, request: web.Request) -> web.Response:
        online = self.bot.is_ready() and self.bot.user is not None
        data: dict[str, Any] = {"bot_online": online}
        if online and self.bot.user is not None:
            data["bot_name"] = self.bot.user.name
        if self.bot.guilds:
            data["guild_name"] = self.bot.guilds[0].name
        return self._json(data)

    async def _api_health(self, request: web.Request) -> web.Response:
        db = self.bot.db
        db_ok = False
        integrity = "not connected"
        if db is not None:
            try:
                integrity = await db.integrity_check()
                db_ok = integrity == "ok"
            except Exception:
                logger.exception("Не удалось проверить целостность БД через /api/health")
                integrity = "error"
        ready = self.bot.is_ready() and self.bot.user is not None
        payload = {
            "ok": ready and db_ok,
            "discord": {"ready": ready, "guilds": len(self.bot.guilds)},
            "database": {"ok": db_ok, "integrity": integrity},
            "backup": self.bot.db_backups.status() if self.bot.db_backups is not None else {"enabled": False},
            "latency_ms": round(self.bot.latency * 1000) if self.bot.latency >= 0 else None,
            "cogs": len(self.bot.cogs),
            "voice_clients": len(self.bot.voice_clients),
            "version": self.bot.config.version,
        }
        return self._json(payload, status=200 if payload["ok"] else 503)

    async def _api_metrics(self, request: web.Request) -> web.Response:
        """Диагностические метрики API и панели для owner/admin."""
        return self._json(
            {
                "ok": True,
                "api_clients": ApiClient.snapshots(),
                "panel": {
                    "active_sessions": len(self._sessions),
                    "rate_limited_ips": sum(1 for hits in self._rate_hits.values() if hits),
                    "analytics_clients": len(self._analytics_clients),
                },
            }
        )

    async def _prometheus_metrics(self, request: web.Request) -> web.Response:
        """Отдаёт компактный Prometheus text exposition через ту же авторизацию."""
        lines = [
            "# HELP clanbot_api_requests_total Total external API requests.",
            "# TYPE clanbot_api_requests_total counter",
            "# HELP clanbot_api_successes_total Successful external API requests.",
            "# TYPE clanbot_api_successes_total counter",
            "# HELP clanbot_api_failures_total Circuit-worthy external API failures.",
            "# TYPE clanbot_api_failures_total counter",
            "# HELP clanbot_api_retries_total External API retries.",
            "# TYPE clanbot_api_retries_total counter",
            "# HELP clanbot_api_circuit_open Whether an API circuit is open.",
            "# TYPE clanbot_api_circuit_open gauge",
        ]
        for item in ApiClient.snapshots():
            service = re.sub(r"[^a-zA-Z0-9_]", "_", str(item["service"]).lower()).strip("_") or "unknown"
            labels = f'service="{service}"'
            lines.extend(
                (
                    f"clanbot_api_requests_total{{{labels}}} {item['requests']}",
                    f"clanbot_api_successes_total{{{labels}}} {item['successes']}",
                    f"clanbot_api_failures_total{{{labels}}} {item['failures']}",
                    f"clanbot_api_retries_total{{{labels}}} {item['retries']}",
                    f"clanbot_api_circuit_open{{{labels}}} {1 if item['circuit_open'] else 0}",
                )
            )
        lines.extend(
            (
                "# HELP clanbot_panel_sessions_active Active panel sessions.",
                "# TYPE clanbot_panel_sessions_active gauge",
                f"clanbot_panel_sessions_active {len(self._sessions)}",
                "# HELP clanbot_panel_analytics_clients Active analytics WebSocket clients.",
                "# TYPE clanbot_panel_analytics_clients gauge",
                f"clanbot_panel_analytics_clients {len(self._analytics_clients)}",
            )
        )
        return web.Response(
            text="\n".join(lines) + "\n",
            content_type="text/plain",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
        )

    async def _api_bot_channels(self, request: web.Request) -> web.Response:
        return self._json({"ok": True, "channels": self._channel_options()})

    # --- API: админка (обзор / настройки) ---

    @staticmethod
    def _human_uptime(seconds: int) -> str:
        days, rem = divmod(max(seconds, 0), 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days:
            return f"{days} д {hours} ч {minutes} мин"
        if hours:
            return f"{hours} ч {minutes} мин"
        return f"{minutes} мин"

    def _primary_guild(self) -> discord.Guild | None:
        configured_id = self.bot.config.guild_id
        if configured_id is not None:
            return self.bot.get_guild(configured_id)
        return self.bot.guilds[0] if self.bot.guilds else None

    def _channel_options(self) -> list[dict[str, Any]]:
        channels: list[dict[str, Any]] = []
        for guild in self.bot.guilds:
            me = guild.me
            for channel in guild.text_channels:
                if me is not None and not channel.permissions_for(me).send_messages:
                    continue
                channels.append(
                    {
                        "id": str(channel.id),
                        "name": channel.name,
                        "category": channel.category.name if channel.category else "",
                        "nsfw": channel.nsfw,
                    }
                )
        channels.sort(key=lambda item: (item["category"], item["name"]))
        return channels

    def _category_options(self) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = []
        for guild in self.bot.guilds:
            for category in guild.categories:
                options.append({"id": str(category.id), "name": category.name})
        options.sort(key=lambda item: item["name"])
        return options

    def _role_options(self) -> list[dict[str, Any]]:
        guild = self._primary_guild()
        if guild is None:
            return []
        roles = []
        for role in guild.roles:
            if role.is_default():
                continue
            roles.append({"id": str(role.id), "name": role.name})
        roles.sort(key=lambda item: item["name"])
        return roles

    def _effective_log_channels(self, settings: dict[str, Any]) -> dict[str, int | None]:
        config = self.bot.config

        def channel_id(value: Any) -> int | None:
            return int(value) if value else None

        return {
            "log": channel_id(settings.get("log_channel_id")),
            "member": channel_id(settings.get("member_log_channel_id") or config.member_log_channel_id),
            "message": channel_id(settings.get("message_log_channel_id") or config.message_log_channel_id),
            "voice": channel_id(settings.get("voice_log_channel_id") or config.voice_log_channel_id),
            "mod": channel_id(settings.get("mod_log_channel_id") or config.mod_log_channel_id),
            "bot": channel_id(settings.get("bot_log_channel_id") or config.bot_log_channel_id),
        }

    def _modules_status(self) -> dict[str, Any]:
        """Витрина модулей, настраиваемых через .env (только для чтения)."""
        config = self.bot.config
        return {
            "birthdays": {
                "channel": config.birthday_channel_id,
                "hour": config.birthday_announce_hour,
                "ping_role": config.birthday_ping_role_id,
            },
            "temp_voices": {
                "triggers": list(config.temp_voice_trigger_ids),
                "category": config.temp_voice_category_id,
            },
            "logs": {
                "ignore_channels": list(config.logs_ignore_channel_ids),
                "ignore_categories": list(config.logs_ignore_category_ids),
            },
            "automod": {
                "block_links": config.automod_block_links,
                "caps_threshold": config.automod_caps_threshold,
                "max_messages": config.automod_max_messages_in_window,
                "timeout_seconds": config.automod_timeout_seconds,
                "ban_after": config.automod_ban_after_timeouts,
                "banned_words": config.automod_banned_words or "",
            },
            "server_stats": {
                "enabled": config.server_stats_enabled,
                "category": config.server_stats_category_name,
                "update_seconds": config.server_stats_update_seconds,
            },
            "rules_gate": {
                "message": config.rules_message_id,
                "role": config.rules_role_id,
            },
            "ram_report": {
                "channel": config.ram_report_channel_id,
                "interval": config.ram_report_interval_minutes,
            },
        }

    async def _api_overview(self, request: web.Request) -> web.Response:
        bot = self.bot
        online = bot.is_ready() and bot.user is not None
        data: dict[str, Any] = {"ok": True, "bot_online": online}
        if online and bot.user is not None:
            data["bot_name"] = bot.user.name
        seconds = int(bot.uptime.total_seconds())
        data["uptime_seconds"] = seconds
        data["uptime"] = self._human_uptime(seconds)
        latency = bot.latency
        data["latency_ms"] = round(latency * 1000) if latency and latency > 0 else 0
        if tracemalloc.is_tracing():
            current, peak = tracemalloc.get_traced_memory()
            data["mem_mb"] = round(current / 1024 / 1024, 1)
            data["mem_peak_mb"] = round(peak / 1024 / 1024, 1)
        else:
            data["mem_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        guild = self._primary_guild()
        if guild is not None:
            data["guild"] = {
                "id": str(guild.id),
                "name": guild.name,
                "members": guild.member_count or len(guild.members),
                "online": sum(1 for member in guild.members if member.status is not discord.Status.offline),
                "channels": len(guild.channels),
                "roles": len(guild.roles),
            }
        now = datetime.now(UTC).isoformat()
        if "latency_ms" in data:
            self._lat.append({"t": now, "v": data.get("latency_ms", 0)})
        if "mem_mb" in data:
            self._mem.append({"t": now, "v": data["mem_mb"]})
        guild = data.get("guild") or {}
        if guild.get("online") is not None:
            self._online.append({"t": now, "v": guild.get("online", 0)})

    async def _api_settings_get(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.settings
        settings = await service.get(guild.id)
        values = {column: (str(settings[column]) if settings.get(column) else "") for column in _SETTING_COLUMNS}
        return self._json(
            {
                "ok": True,
                "guild_id": str(guild.id),
                "guild_name": guild.name,
                "settings": values,
                "automod_enabled": bool(settings.get("automod_enabled")),
                "blocked_words": await service.blocked_words(guild.id),
                "channels": self._channel_options(),
                "categories": self._category_options(),
                "roles": self._role_options(),
                "modules": self._modules_status(),
                "effective_logs": self._effective_log_channels(settings),
            }
        )

    async def _api_settings_post(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        service = self.services.settings
        values: dict[str, Any] = {}
        for column in _SETTING_COLUMNS:
            if column not in payload:
                continue
            raw = payload.get(column)
            if raw in (None, "", 0):
                values[column] = None
                continue
            try:
                values[column] = int(raw)
            except (TypeError, ValueError):
                return self._json({"ok": False, "error": f"Некорректный ID канала: {column}"}, status=400)
        if "automod_enabled" in payload:
            values["automod_enabled"] = 1 if payload.get("automod_enabled") else 0
        if values:
            await service.update(guild.id, **values)
        if "blocked_words" in payload:
            words = payload.get("blocked_words") or []
            if isinstance(words, str):
                words = re.split(r"[\n,]+", words)
            await service.set_blocked_words(guild.id, list(words))
        return self._json({"ok": True})

    # --- API: загрузка изображений для эмбедов ---

    async def _api_upload(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        field = await reader.next()
        if field is None:
            return self._json({"ok": False, "error": "Файл не передан"}, status=400)
        ext = Path(field.filename or "").suffix.lower()
        if ext not in _UPLOAD_EXTS:
            return self._json({"ok": False, "error": "Допустимы только PNG, JPG, GIF, WEBP"}, status=400)
        data = b""
        while len(data) <= _MAX_UPLOAD_BYTES:
            chunk = await field.read_chunk()
            if not chunk:
                break
            data += chunk
        if not data:
            return self._json({"ok": False, "error": "Пустой файл"}, status=400)
        if len(data) > _MAX_UPLOAD_BYTES:
            return self._json({"ok": False, "error": "Файл больше 8 МБ"}, status=400)
        signatures = _MAGIC.get(ext, ())
        if not signatures or not any(data[: len(sig)] == sig for sig in signatures):
            return self._json({"ok": False, "error": "Содержимое не соответствует типу файла"}, status=400)
        if ext == ".webp" and data[8:12] != b"WEBP":
            return self._json({"ok": False, "error": "Содержимое не соответствует типу файла"}, status=400)
        self._uploads_dir.mkdir(parents=True, exist_ok=True)
        name = uuid.uuid4().hex + ext
        (self._uploads_dir / name).write_bytes(data)
        base = self._public_base(request)
        return self._json({"ok": True, "name": name, "url": f"/uploads/{name}", "absolute_url": f"{base}/uploads/{name}"})

    async def _api_uploads_list(self, request: web.Request) -> web.Response:
        files: list[dict[str, Any]] = []
        if self._uploads_dir.is_dir():
            for path in sorted(self._uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
                if not path.is_file() or _UPLOAD_NAME_RE.match(path.name) is None:
                    continue
                size = path.stat().st_size
                files.append(
                    {
                        "name": path.name,
                        "url": f"/uploads/{path.name}",
                        "size": self._human_size(size),
                        "bytes": size,
                    }
                )
        return self._json({"ok": True, "count": len(files), "files": files})

    async def _api_uploads_delete(self, request: web.Request) -> web.Response:
        name = request.match_info.get("name", "")
        if _UPLOAD_NAME_RE.match(name) is None:
            return self._json({"ok": False, "error": "Некорректное имя файла"}, status=400)
        path = self._uploads_dir / name
        if not path.is_file():
            return self._json({"ok": False, "error": "Файл не найден"}, status=404)
        path.unlink(missing_ok=True)
        return self._json({"ok": True})

    @staticmethod
    def _human_size(size: int) -> str:
        if size < 1024:
            return f"{size} Б"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} КБ"
        return f"{size / 1024 / 1024:.1f} МБ"

    @staticmethod
    def _public_base(request: web.Request) -> str:
        proto = request.headers.get("X-Forwarded-Proto") or request.scheme
        return f"{proto}://{request.host}"

    # --- API: отправка через бота ---

    def _resolve_channel(self, channel_id: Any) -> discord.TextChannel | None:
        try:
            channel = self.bot.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _api_bot_send(self, request: web.Request) -> web.Response:
        payload = await self._read_json(request)
        channel = self._resolve_channel(payload.get("channel_id") or "")
        if channel is None:
            return self._json({"ok": False, "error": "Канал не найден"}, status=400)
        try:
            message = await channel.send(
                content=str(payload.get("content") or "")[:2000] or None,
                embeds=[_embed_from_dict(item) for item in (payload.get("embeds") or [])[:_MAX_EMBEDS] if isinstance(item, dict)],
                view=_view_from_components(payload.get("components")),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, discord.Forbidden) as exc:
            return self._json({"ok": False, "error": str(exc)}, status=400)
        return self._json({"ok": True, "message_id": str(message.id)})

    async def _api_bot_edit(self, request: web.Request) -> web.Response:
        payload = await self._read_json(request)
        channel = self._resolve_channel(payload.get("channel_id") or "")
        message_id = self._parse_id(payload.get("message_id"))
        if channel is None or message_id is None:
            return self._json({"ok": False, "error": "Канал или ID сообщения неверны"}, status=400)
        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return self._json({"ok": False, "error": "Сообщение не найдено"}, status=404)
        try:
            await message.edit(
                content=str(payload.get("content") or "")[:2000] or None,
                embeds=[_embed_from_dict(item) for item in (payload.get("embeds") or [])[:_MAX_EMBEDS] if isinstance(item, dict)],
                view=_view_from_components(payload.get("components")),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.HTTPException, discord.Forbidden) as exc:
            return self._json({"ok": False, "error": str(exc)}, status=400)
        return self._json({"ok": True})

    async def _api_bot_fetch(self, request: web.Request) -> web.Response:
        payload = await self._read_json(request)
        channel = self._resolve_channel(payload.get("channel_id") or "")
        message_id = self._parse_id(payload.get("message_id"))
        if channel is None or message_id is None:
            return self._json({"ok": False, "error": "Канал или ID сообщения неверны"}, status=400)
        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return self._json({"ok": False, "error": "Сообщение не найдено"}, status=404)
        return self._json({"ok": True, "data": _message_to_client(message)})

    # --- API: мониторинг, сервер, модерация, розыгрыши, планировщик, бэкап ---

    # --- сбор сообщений для /api/stats ---

    async def _on_message_hook(self, message: discord.Message) -> None:
        """Корутина дяди discord.py: add_listener требует async-функцию, не обёртку."""
        await self._msg_counter(message)

    async def _msg_counter(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        now = datetime.now(UTC)
        hour_key = now.replace(minute=0, second=0, microsecond=0)
        day_key = now.date().isoformat()
        self._msg_total += 1
        if self._msg_by_hour and self._msg_by_hour[-1]["t"] == hour_key.timestamp():
            self._msg_by_hour[-1]["n"] += 1
        else:
            self._msg_by_hour.append({"t": hour_key.timestamp(), "n": 1})
        if self._msg_by_day and self._msg_by_day[-1].get("d") == day_key:
            self._msg_by_day[-1]["n"] += 1
        else:
            self._msg_by_day.append({"d": day_key, "n": 1})
        self._pending_activity[(message.guild.id, hour_key.isoformat())] += 1

    async def _analytics_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            await self._flush_activity()

    async def _flush_activity(self) -> None:
        if self.bot.db is None or not self._pending_activity:
            return
        pending = self._pending_activity
        self._pending_activity = defaultdict(int)
        for (guild_id, bucket), amount in pending.items():
            try:
                await self.bot.db.increment_activity(guild_id, bucket, amount)
            except Exception:
                self._pending_activity[(guild_id, bucket)] += amount
                logger.exception("Не удалось сохранить activity metric")
        if self._analytics_clients:
            payload = await self._analytics_payload()
            dead: list[web.WebSocketResponse] = []
            for websocket in self._analytics_clients:
                try:
                    await websocket.send_json({"type": "analytics", "data": payload})
                except (ConnectionResetError, RuntimeError, aiohttp.ClientConnectionError):
                    dead.append(websocket)
            for websocket in dead:
                self._analytics_clients.discard(websocket)

    async def _analytics_payload(self) -> dict[str, Any]:
        guild = self._primary_guild()
        if guild is None or self.bot.db is None:
            return {"guild_id": None, "rows": []}
        rows = await self.bot.db.list_activity(guild.id, 1000)
        return {"guild_id": str(guild.id), "rows": rows}

    async def _api_analytics(self, request: web.Request) -> web.Response:
        await self._flush_activity()
        return self._json({"ok": True, **(await self._analytics_payload())})

    async def _api_analytics_export(self, request: web.Request) -> web.Response:
        await self._flush_activity()
        payload = await self._analytics_payload()
        rows = payload["rows"]
        if request.query.get("format", "json").lower() == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["bucket", "messages"])
            writer.writeheader()
            writer.writerows(rows)
            return web.Response(
                text=output.getvalue(),
                content_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=activity.csv"},
            )
        return self._json({"ok": True, **payload})

    async def _ws_analytics(self, request: web.Request) -> web.StreamResponse:
        if not self._rate_ok(request) or not self._allowed_origin(request):
            return self._json({"ok": False, "error": "Unauthorized"}, status=401)
        websocket = web.WebSocketResponse(heartbeat=30)
        await websocket.prepare(request)
        try:
            first = await websocket.receive(timeout=5)
        except TimeoutError:
            await websocket.close(code=4401, message=b"Unauthorized")
            return websocket
        token = ""
        if first.type == web.WSMsgType.TEXT:
            try:
                token = str(json.loads(first.data).get("token") or "")
            except (TypeError, ValueError):
                token = ""
        role = self._check_token_value(token)
        if role is None or self._role_rank(role) < self._role_rank("viewer"):
            await websocket.close(code=4401, message=b"Unauthorized")
            return websocket
        self._analytics_clients.add(websocket)
        try:
            await websocket.send_json({"type": "analytics", "data": await self._analytics_payload()})
            async for message in websocket:
                if message.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                    break
        finally:
            self._analytics_clients.discard(websocket)
        return websocket

    async def _api_monitor(self, request: web.Request) -> web.Response:
        sample = await self._live_sample()
        self._record_metrics(sample)
        return self._json(
            {
                "ok": True,
                "latency": list(self._lat),
                "mem": list(self._mem),
                "online": list(self._online),
                **sample,
            }
        )

    async def _api_stats(self, request: web.Request) -> web.Response:
        sample = await self._live_sample()
        self._record_metrics(sample)
        return self._json(
            {
                "ok": True,
                "by_hour": list(self._msg_by_hour),
                "by_day": list(self._msg_by_day),
                "total": self._msg_total,
                "latency": list(self._lat),
                "mem": list(self._mem),
                "online": list(self._online),
                **sample,
            }
        )

    async def _live_sample(self) -> dict[str, Any]:
        """Снимает актуальный срез латентности/памяти/онлайна для мониторинга."""
        bot = self.bot
        sample: dict[str, Any] = {}
        latency = bot.latency
        sample["latency_ms"] = round(latency * 1000) if latency and latency > 0 else 0
        sample["mem_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        guild = self._primary_guild()
        if guild is not None:
            sample["guild"] = {"online": sum(1 for member in guild.members if member.status is not discord.Status.offline)}
        return sample

    def _resolve_member(self, guild: discord.Guild, value: Any) -> discord.Member | None:
        if value in (None, ""):
            return None
        raw = str(value).strip()
        match = re.match(r"^<@!?(\d+)>$", raw)
        if match:
            raw = match.group(1)
        if raw.isdigit():
            member = guild.get_member(int(raw))
            if member is not None:
                return member
        name = raw.lstrip("@").lower()
        candidates = [
            m
            for m in guild.members
            if m.display_name.lower() == name or m.name.lower() == name or f"{m.name}#{m.discriminator}".lower() == name
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _resolve_role(self, guild: discord.Guild, value: Any) -> discord.Role | None:
        if value in (None, ""):
            return None
        raw = str(value).strip()
        match = re.match(r"^<@&(\d+)>$", raw)
        if match:
            raw = match.group(1)
        if raw.isdigit():
            role = guild.get_role(int(raw))
            if role is not None:
                return role
        name = raw.lstrip("@").lower()
        matching = [r for r in guild.roles if r.name.lower() == name]
        return matching[0] if len(matching) == 1 else None

    def _guard_mod_target(self, guild: discord.Guild, member: discord.Member) -> str | None:
        from app.core.checks import can_moderate

        me = guild.me
        if me is None:
            return "Бот недоступен на сервере"
        if member.id == guild.owner_id:
            return "Нельзя: владелец сервера"
        if member.top_role.position >= me.top_role.position:
            return "Бот не может модернировать: роли участника выше ролей бота"
        if not can_moderate(me, member):
            return "Бот не может модернировать этого участника"
        return None

    def _member_payload(self, guild: discord.Guild, member: discord.Member) -> dict[str, Any]:
        return {
            "id": str(member.id),
            "name": member.name,
            "display_name": member.display_name,
            "tag": str(member),
            "is_bot": member.bot,
            "status": str(member.status) if member.status is not None else "unknown",
            "top_role": member.top_role.name,
            "top_role_color": f"#{member.top_role.color.value:06x}" if member.top_role and member.top_role.color.value else "#99aab5",
            "avatar": member.display_avatar.url,
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "warnings": 0,
        }

    async def _api_server(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        me = guild.me
        categories: list[dict[str, Any]] = []
        for category in sorted(guild.categories, key=lambda c: c.position):
            channels: list[dict[str, Any]] = []
            for channel in sorted(category.channels, key=lambda c: c.position):
                if isinstance(channel, discord.VoiceChannel):
                    voice_users = [m for m in channel.members if not m.bot]
                    channels.append(
                        {
                            "id": str(channel.id),
                            "name": channel.name,
                            "type": "voice",
                            "topic": "",
                            "slowmode": 0,
                            "nsfw": False,
                            "voice_online": len(voice_users),
                            "voice_users": [self._member_payload(guild, m) for m in voice_users[:30]],
                        }
                    )
                elif isinstance(channel, discord.TextChannel):
                    channels.append(
                        {
                            "id": str(channel.id),
                            "name": channel.name,
                            "type": channel.type.name,
                            "topic": (channel.topic or "")[:200],
                            "slowmode": channel.slowmode_delay,
                            "nsfw": channel.nsfw,
                            "voice_online": 0,
                            "voice_users": [],
                        }
                    )
            categories.append(
                {
                    "id": str(category.id),
                    "name": category.name,
                    "position": category.position,
                    "channels": channels,
                }
            )
        uncategorized: list[dict[str, Any]] = []
        for channel in guild.channels:
            if isinstance(channel, discord.TextChannel) and channel.category_id is None:
                uncategorized.append(
                    {
                        "id": str(channel.id),
                        "name": channel.name,
                        "type": channel.type.name,
                        "topic": (channel.topic or "")[:200],
                        "slowmode": channel.slowmode_delay,
                        "nsfw": channel.nsfw,
                        "voice_online": 0,
                        "voice_users": [],
                    }
                )
        if uncategorized:
            categories.append({"id": "", "name": "Без категории", "position": 9999, "channels": uncategorized})
        roles = []
        for role in reversed(guild.roles):
            if role.is_default():
                continue
            roles.append(
                {
                    "id": str(role.id),
                    "name": role.name,
                    "color": f"#{role.color.value:06x}" if role.color.value else "#99aab5",
                    "position": role.position,
                    "member_count": len(role.members),
                    "hoist": role.hoist,
                    "mentionable": role.mentionable,
                    "managed": role.managed,
                    "bot_managed": role.is_bot_managed(),
                }
            )
        online = sum(1 for m in guild.members if m.status is not discord.Status.offline)
        return self._json(
            {
                "ok": True,
                "guild": {
                    "id": str(guild.id),
                    "name": guild.name,
                    "icon": guild.icon.url if guild.icon else None,
                    "description": guild.description,
                    "members": guild.member_count or len(guild.members),
                    "online": online,
                    "channels": len(guild.channels),
                    "roles": len(guild.roles),
                    "boosts": guild.premium_subscription_count or 0,
                    "level": f"Уровень {guild.premium_tier}",
                    "owner": guild.owner.display_name if guild.owner else None,
                    "created_at": guild.created_at.isoformat(),
                    "me_name": me.display_name if me else None,
                    "me_permissions": self._bot_permission_summary(guild),
                },
                "categories": categories,
                "roles": roles,
            }
        )

    def _bot_permission_summary(self, guild: discord.Guild) -> list[str]:
        if guild.me is None:
            return []
        perms = guild.me.guild_permissions
        mapping = {
            "kick_members": "Кик",
            "ban_members": "Бан",
            "moderate_members": "Тайм-ауты",
            "manage_roles": "Роли",
            "manage_channels": "Каналы",
            "manage_messages": "Сообщения",
            "manage_guild": "Управление сервером",
            "administrator": "Администратор",
        }
        return [label for flag, label in mapping.items() if getattr(perms, flag, False)]

    async def _api_server_members(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        query = (request.query.get("q") or "").strip().lower()
        members = list(guild.members)
        if query:
            if query.isdigit():
                member = guild.get_member(int(query))
                members = [member] if member else []
            else:
                members = [m for m in members if query in m.name.lower() or query in m.display_name.lower() or query in str(m).lower()]
        members.sort(key=lambda m: (m.bot, m.display_name.lower()))
        service = self.services.moderation
        payload = []
        for member in members[:25]:
            item = self._member_payload(guild, member)
            item["warnings"] = await service.warn_count(guild.id, member.id)
            payload.append(item)
        return self._json({"ok": True, "total": len(payload), "members": payload})

    async def _api_server_members_roles(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        member = self._resolve_member(guild, payload.get("member"))
        role = self._resolve_role(guild, payload.get("role_id"))
        if member is None or role is None:
            return self._json({"ok": False, "error": "Участник или роль не найдены"}, status=400)
        action = str(payload.get("action") or "add")
        if guild.me is None or not guild.me.guild_permissions.manage_roles:
            return self._json({"ok": False, "error": "У бота нет права manage_roles"}, status=400)
        if not role.is_assignable():
            return self._json({"ok": False, "error": "Роль нельзя выдавать (интегрированная или выше ролей бота)"}, status=400)
        try:
            if action == "remove" and role in member.roles:
                await member.remove_roles(role, reason="Вебпанель: снятие роли")
                applied = False
            elif action == "remove":
                applied = False
            elif action == "add" and role not in member.roles:
                await member.add_roles(role, reason="Вебпанель: выдача роли")
                applied = True
            else:
                applied = role in member.roles
        except discord.HTTPException as exc:
            return self._json({"ok": False, "error": f"Discord: {exc.status} {exc.text}"[:200]}, status=400)
        return self._json(
            {
                "ok": True,
                "member_id": str(member.id),
                "member_name": member.display_name,
                "role_id": str(role.id),
                "role_name": role.name,
                "action": action,
                "applied": applied,
                "has_role": role in member.roles,
            }
        )

    async def _api_warns(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.moderation
        user_value = request.query.get("user_id") or (request.query.get("q") or "")
        warns = await service.all_warns(guild.id, 300)
        if user_value:
            member = self._resolve_member(guild, user_value)
            if member is None:
                return self._json({"ok": False, "error": "Участник не найден"}, status=404)
            warns = [w for w in warns if w["user_id"] == member.id]
        enriched = [
            {
                "id": w["id"],
                "user_id": str(w["user_id"]),
                "user_name": self._member_name(guild, w["user_id"]),
                "moderator_id": str(w["moderator_id"]),
                "moderator_name": self._member_name(guild, w["moderator_id"]),
                "reason": w["reason"],
                "created_at": w["created_at"],
            }
            for w in warns
        ]
        return self._json({"ok": True, "total": len(enriched), "warns": enriched})

    def _member_name(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member is not None:
            return member.display_name
        if user_id == self.bot.user.id:
            return "Бот (панель)"
        return str(user_id)

    def _member_options(self, guild: discord.Guild, limit: int = 0) -> list[dict[str, Any]]:
        members = [member for member in guild.members if not member.bot]
        members.sort(key=lambda member: member.display_name.lower())
        if limit and len(members) > limit:
            members = members[:limit]
        return [{"id": str(member.id), "name": member.display_name} for member in members]

    async def _api_warn_add(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        member = self._resolve_member(guild, payload.get("member"))
        if member is None:
            return self._json({"ok": False, "error": "Участник не найден"}, status=404)
        guard = self._guard_mod_target(guild, member)
        if guard:
            return self._json({"ok": False, "error": guard}, status=400)
        reason = str(payload.get("reason") or "Без причины")[:500]
        service = self.services.moderation
        count = await service.warn(guild.id, member.id, self.bot.user.id, reason)
        if guild.me is not None:
            await self.services.logging.log_mod_action(
                guild, "warn", member, guild.me, reason, description=f"{member.mention} получил предупреждение {count} (панель)"
            )
        return self._json({"ok": True, "count": count, "member": self._member_payload(guild, member)})

    async def _api_warn_delete(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        warn_id = self._parse_id(request.match_info.get("warn_id"))
        service = self.services.moderation
        if warn_id is None or (warn := await service.get_warn(guild.id, warn_id)) is None:
            return self._json({"ok": False, "error": "Предупреждение не найдено"}, status=404)
        await service.remove_warn(guild.id, warn_id)
        return self._json(
            {
                "ok": True,
                "warn_id": warn_id,
                "user_id": str(warn["user_id"]),
                "action": "removed",
                "next_count": await service.warn_count(guild.id, warn["user_id"]),
            }
        )

    async def _api_warn_clear(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        member = self._resolve_member(guild, payload.get("member"))
        if member is None:
            return self._json({"ok": False, "error": "Участник не найден"}, status=404)
        service = self.services.moderation
        cleared = await service.clear_warns(guild.id, member.id)
        return self._json({"ok": True, "cleared": cleared, "member_id": str(member.id)})

    async def _api_mod_kick(self, request: web.Request) -> web.Response:
        return await self._mod_action(request, "kick")

    async def _api_mod_ban(self, request: web.Request) -> web.Response:
        return await self._mod_action(request, "ban", delete_days=True)

    async def _api_mod_unban(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        if guild.me is None or not guild.me.guild_permissions.ban_members:
            return self._json({"ok": False, "error": "У бота нет права ban_members"}, status=400)
        user_id = self._parse_id(payload.get("user_id"))
        if user_id is None:
            return self._json({"ok": False, "error": "Неверный ID пользователя"}, status=400)
        try:
            ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
        except discord.NotFound:
            return self._json({"ok": False, "error": "Пользователь не в бане"}, status=404)
        reason = str(payload.get("reason") or "Разбан из вебпанели")
        await guild.unban(ban_entry.user, reason=f"Вебпанель | {reason}")
        if guild.me is not None:
            await self.services.logging.log_mod_action(
                guild, "unban", ban_entry.user, guild.me, reason, description=f"{ban_entry.user} разбанен (панель)"
            )
        return self._json({"ok": True, "user_id": str(user_id), "user_name": str(ban_entry.user)})

    async def _api_mod_timeout(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        if guild.me is None or not guild.me.guild_permissions.moderate_members:
            return self._json({"ok": False, "error": "У бота нет права moderate_members"}, status=400)
        member = self._resolve_member(guild, payload.get("member"))
        if member is None:
            return self._json({"ok": False, "error": "Участник не найден"}, status=404)
        guard = self._guard_mod_target(guild, member)
        if guard:
            return self._json({"ok": False, "error": guard}, status=400)
        try:
            seconds = int(payload.get("duration_seconds") or 600)
        except (TypeError, ValueError):
            seconds = 600
        seconds = max(30, min(seconds, 28 * 86400))
        reason = str(payload.get("reason") or "Тайм-аут из вебпанели")
        end = datetime.now(UTC) + timedelta(seconds=seconds)
        await member.timeout(end, reason=f"Вебпанель | {reason}")
        if guild.me is not None:
            await self.services.logging.log_mod_action(
                guild,
                "timeout",
                member,
                guild.me,
                reason,
                description=f"{member.mention} получил тайм-аут {seconds // 60} мин (панель)",
            )
        return self._json({"ok": True, "member_id": str(member.id), "member_name": member.display_name, "until": end.isoformat()})

    async def _mod_action(self, request: web.Request, action: str, *, delete_days: bool = False) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        if guild.me is None or not getattr(guild.me.guild_permissions, f"{action}_members"):
            return self._json({"ok": False, "error": f"У бота нет права {action}_members"}, status=400)
        member = self._resolve_member(guild, payload.get("member"))
        if member is None:
            return self._json({"ok": False, "error": "Участник не найден"}, status=404)
        guard = self._guard_mod_target(guild, member)
        if guard:
            return self._json({"ok": False, "error": guard}, status=400)
        reason = str(payload.get("reason") or "")
        fmt = "кикнут"
        extra: dict[str, Any] = {}
        if action == "kick":
            await member.kick(reason=f"Вебпанель{': ' + reason if reason else ''}")
        else:
            fmt = "забанен"
            delete_days_int = 0
            if delete_days:
                try:
                    delete_days_int = max(0, min(int(payload.get("delete_days") or 0), 7))
                except (TypeError, ValueError):
                    delete_days_int = 0
            await member.ban(reason=f"Вебпанель{': ' + reason if reason else ''}", delete_message_seconds=delete_days_int * 86400)
            extra["delete_days"] = delete_days_int
        if guild.me is not None:
            await self.services.logging.log_mod_action(
                guild, action, member, guild.me, reason, description=f"{member.mention} {fmt} (панель)"
            )
        return self._json({"ok": True, "action": action, "member_id": str(member.id), "member_name": member.display_name, **extra})

    async def _api_giveaways(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.giveaways
        rows = await service.recent_for_guild(guild.id, 60)
        active: list[dict[str, Any]] = []
        finished: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "id": row["id"],
                "prize": row["prize"],
                "winners": row["winners"],
                "active": bool(row["active"]),
                "message_id": str(row["message_id"]) if row.get("message_id") else None,
                "ends_at": row["ends_at"],
                "created_at": row["created_at"],
                "author_name": self._member_name(guild, row["author_id"]),
                "channel_name": self._channel_name(guild, row["channel_id"]),
                "entries": len(await service.entries(row["id"])),
                "min_days": row.get("min_days", 0),
            }
            (active if row["active"] else finished).append(item)
        return self._json(
            {
                "ok": True,
                "active_total": len(active),
                "finished_total": len(finished),
                "active": active,
                "finished": finished,
            }
        )

    def _channel_name(self, guild: discord.Guild, channel_id: int) -> str:
        channel = guild.get_channel(channel_id)
        if channel is None:
            channel = self.bot.get_channel(channel_id)
        if channel is None:
            return str(channel_id)
        prefix = "🔊" if isinstance(channel, discord.VoiceChannel) else "#"
        return f"{prefix} {channel.name}"

    async def _api_giveaway_create(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        channel = self._resolve_channel(payload.get("channel_id") or "")
        if channel is None or channel.guild.id != guild.id:
            return self._json({"ok": False, "error": "Канал не найден на этом сервере"}, status=400)
        prize = str(payload.get("prize") or "").strip()
        if not prize:
            return self._json({"ok": False, "error": "Укажите приз"}, status=400)
        try:
            winners = int(payload.get("winners") or 1)
            minutes = int(payload.get("duration_minutes") or 60)
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "Количество победителей/длительность — числа"}, status=400)
        if not (1 <= winners <= 20):
            return self._json({"ok": False, "error": "Победителей: от 1 до 20"}, status=400)
        if not (1 <= minutes <= 43200):
            return self._json({"ok": False, "error": "Длительность: от 1 минуты до 30 дней"}, status=400)
        try:
            min_days = max(0, int(payload.get("min_days") or 0))
        except (TypeError, ValueError):
            min_days = 0
        service = self.services.giveaways
        ends_at = datetime.now(UTC) + timedelta(minutes=minutes)
        if self.bot.user is None:
            return self._json({"ok": False, "error": "Бот ещё не готов"}, status=503)
        giveaway_id = await service.create(guild.id, channel.id, self.bot.user.id, prize[:256], winners, ends_at, min_days)
        giveaway = await service.get(giveaway_id)
        if giveaway is None:
            await service.finish(giveaway_id)
            return self._json({"ok": False, "error": "Не удалось загрузить созданный розыгрыш"}, status=500)
        embed = await service.embed(giveaway)
        view = self._giveaway_view()
        try:
            message = await channel.send(embed=embed, view=view)
        except discord.HTTPException as exc:
            await service.finish(giveaway_id)
            return self._json({"ok": False, "error": f"Не удалось отправить: {exc.status}"}, status=400)
        await service.bind_message(giveaway_id, message.id)
        self.bot.add_view(view, message_id=message.id)
        return self._json(
            {
                "ok": True,
                "id": giveaway_id,
                "message_id": str(message.id),
                "channel": channel.name,
                "jump_url": message.jump_url,
                "ends_at": ends_at.isoformat(),
            }
        )

    def _giveaway_view(self) -> Any:
        from app.core.views import GiveawayView

        return GiveawayView()

    async def _finish_giveaway(self, giveaway: GiveawayRow, *, reroll: bool = False) -> str:
        """Завершает розыгрыш: розыгрыш победителей, анонс, обновление сообщения."""
        service = self.services.giveaways
        entries = await service.entries(giveaway["id"])
        winners = service.draw(entries, int(giveaway["winners"]))
        await service.finish(giveaway["id"])
        channel = self.bot.get_channel(giveaway["channel_id"])
        if isinstance(channel, discord.TextChannel):
            header = "Перерозыгрыш" if reroll else "Приз"
            announce = embeds.success("🎉 Розыгрыш завершён", f"{header}: **{giveaway['prize']}**")
            announce.add_field(
                name="Победители",
                value=", ".join(f"<@{uid}>" for uid in winners) if winners else "Недостаточно участников",
                inline=False,
            )
            try:
                await channel.send(embed=announce)
            except discord.HTTPException:
                pass
            message_id = giveaway.get("message_id")
            if message_id is not None:
                try:
                    message = await channel.fetch_message(message_id)
                    embed = await service.embed(giveaway)
                    embed.set_footer(text="Розыгрыш завершён")
                    await message.edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass
        return ", ".join(f"<@{uid}>" for uid in winners) if winners else "нет победителей"

    async def _api_giveaway_end(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        payload = await self._read_json(request)
        message_id = self._parse_id(payload.get("message_id"))
        if guild is None or message_id is None:
            return self._json({"ok": False, "error": "Сервер или ID сообщения неверны"}, status=400)
        service = self.services.giveaways
        giveaway = await service.get_by_message(message_id)
        if giveaway is None or giveaway["guild_id"] != guild.id:
            return self._json({"ok": False, "error": "Розыгрыш не найден"}, status=404)
        if not giveaway["active"]:
            return self._json({"ok": False, "error": "Розыгрыш уже завершён"}, status=400)
        winners = await self._finish_giveaway(giveaway)
        return self._json({"ok": True, "winners": winners, "message_id": str(message_id)})

    async def _api_giveaway_reroll(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        payload = await self._read_json(request)
        message_id = self._parse_id(payload.get("message_id"))
        if guild is None or message_id is None:
            return self._json({"ok": False, "error": "Сервер или ID сообщения неверны"}, status=400)
        service = self.services.giveaways
        giveaway = await service.get_by_message(message_id)
        if giveaway is None or giveaway["guild_id"] != guild.id:
            return self._json({"ok": False, "error": "Розыгрыш не найден"}, status=404)
        winners = await self._finish_giveaway(giveaway, reroll=True)
        return self._json({"ok": True, "winners": winners, "message_id": str(message_id)})

    async def _api_tickets_panel_get(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        settings = await self.services.settings.get(guild.id)
        category_id = settings.get("ticket_category_id")
        categories = [{"id": str(c.id), "name": c.name} for c in sorted(guild.categories, key=lambda c: c.position)]
        texts = {field: (settings.get(field) or _TICKET_DEFAULTS[field]) for field in _TICKET_TEXT_FIELDS}
        return self._json(
            {
                "ok": True,
                "category_id": str(category_id) if category_id else "",
                "categories": categories,
                "channels": self._channel_options(),
                "texts": texts,
            }
        )

    async def _api_tickets_panel_post(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        service = self.services.tickets
        settings_service = self.services.settings

        made: list[str] = []
        raw_category = payload.get("category_id")
        if raw_category not in (None, ""):
            category_id = self._parse_id(raw_category)
            if category_id is None or not isinstance(guild.get_channel(category_id), discord.CategoryChannel):
                return self._json({"ok": False, "error": "Категория не найдена"}, status=400)
            await settings_service.update(guild.id, ticket_category_id=category_id)
            made.append("category")

        settings = await settings_service.get(guild.id)
        text_updates = {f: payload[f] for f in _TICKET_TEXT_FIELDS if f in payload}
        if text_updates:
            updates = {}
            for field, value in text_updates.items():
                value = str(value).strip()
                updates[field] = value if value else _TICKET_DEFAULTS[field]
            if updates:
                await settings_service.update(guild.id, **updates)
                made.append("texts")
            settings = await settings_service.get(guild.id)

        panel_sent = False
        raw_channel = payload.get("channel_id")
        if raw_channel not in (None, ""):
            channel = self._resolve_channel(raw_channel)
            if channel is None or channel.guild.id != guild.id:
                return self._json({"ok": False, "error": "Канал не найден на этом сервере"}, status=400)
            from app.core.views import TicketOpenView

            embed = embeds.info(
                settings.get("ticket_panel_title") or _TICKET_DEFAULTS["ticket_panel_title"],
                settings.get("ticket_panel_description") or _TICKET_DEFAULTS["ticket_panel_description"],
            )
            embed.set_footer(text=settings.get("ticket_panel_footer") or _TICKET_DEFAULTS["ticket_panel_footer"])
            try:
                await channel.send(
                    embed=embed,
                    view=TicketOpenView(
                        service,
                        label=settings.get("ticket_open_label") or _TICKET_DEFAULTS["ticket_open_label"],
                        emoji=settings.get("ticket_open_emoji"),
                    ),
                )
            except (discord.HTTPException, discord.Forbidden) as exc:
                return self._json({"ok": False, "error": str(exc)}, status=400)
            panel_sent = True
            made.append("panel")

        if not made:
            return self._json({"ok": False, "error": "Не указано, что обновить"}, status=400)
        return self._json({"ok": True, "made": made, "panel_sent": panel_sent})

    async def _api_tickets(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.tickets
        rows = await service.list_tickets(guild.id, 300)
        open_items: list[dict[str, Any]] = []
        closed_items: list[dict[str, Any]] = []
        for row in rows:
            channel = guild.get_channel(row["channel_id"])
            item: dict[str, Any] = {
                "id": row["ticket_id"],
                "creator_id": str(row["creator_id"]),
                "creator_name": self._member_name(guild, row["creator_id"]),
                "channel_id": str(row["channel_id"]),
                "channel_name": channel.name if channel else None,
                "channel_mention": f"<#{row['channel_id']}>",
                "status": row["status"],
                "created_at": row["created_at"],
                "closed_at": row.get("closed_at"),
                "has_transcript": bool(row.get("transcript")),
                "message_count": (row.get("transcript") or "").count("\n") if row.get("transcript") else 0,
            }
            (closed_items if row["status"] != "open" else open_items).append(item)
        return self._json(
            {
                "ok": True,
                "open_total": len(open_items),
                "closed_total": len(closed_items),
                "open": open_items,
                "closed": closed_items,
            }
        )

    async def _api_ticket_close(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        ticket_id = self._parse_id(request.match_info.get("ticket_id"))
        if ticket_id is None:
            return self._json({"ok": False, "error": "Неверный № тикета"}, status=400)
        service = self.services.tickets
        closer: discord.Member | discord.ClientUser = guild.me or self.bot.user
        if closer is None:
            return self._json({"ok": False, "error": "Бот не авторизован на сервере"}, status=400)
        result = await service.close_by_id(guild, ticket_id, closer)
        if result.error:
            return self._json({"ok": False, "error": result.error}, status=400)
        return self._json({"ok": True, "transcript_channel_mention": result.transcript_channel_mention})

    async def _api_ticket_transcript(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        ticket_id = self._parse_id(request.match_info.get("ticket_id"))
        if ticket_id is None:
            return self._json({"ok": False, "error": "Неверный № тикета"}, status=400)
        service = self.services.tickets
        ticket = await service.get_ticket(ticket_id)
        if ticket is None or ticket["guild_id"] != guild.id:
            return self._json({"ok": False, "error": "Тикет не найден"}, status=404)
        text = ticket.get("transcript") or ""
        return web.Response(
            text=text,
            content_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="ticket-{ticket_id}.txt"',
                "Cache-Control": "no-store",
            },
        )

    # --- API: автомод ---

    async def _api_automod_get(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.settings
        settings = await service.get(guild.id)
        config = self.bot.config
        ignored_channels = [{"id": str(cid), "name": self._channel_name(guild, cid)} for cid in config.automod_ignored_channels]
        return self._json(
            {
                "ok": True,
                "enabled": bool(config.automod_enabled and settings.get("automod_enabled", True)),
                "config_enabled": bool(config.automod_enabled),
                "db_enabled": bool(settings.get("automod_enabled", True)),
                "blocked_words": await service.blocked_words(guild.id),
                "env": {
                    "banned_words": config.automod_banned_words or "",
                    "block_links": bool(config.automod_block_links),
                    "allowed_links": config.automod_allowed_links or "",
                    "caps_threshold": config.automod_caps_threshold,
                    "caps_min_len": config.automod_caps_min_len,
                    "max_messages": config.automod_max_messages_in_window,
                    "timeout_seconds": config.automod_timeout_seconds,
                    "ban_after": config.automod_ban_after_timeouts,
                    "ban_window": config.automod_ban_window_seconds,
                    "ignore_roles": list(config.automod_ignore_roles),
                    "ignored_channels": ignored_channels,
                    "anti_raid": {
                        "enabled": config.automod_antiraid_enabled,
                        "window_seconds": config.automod_antiraid_window_seconds,
                        "join_threshold": config.automod_antiraid_join_threshold,
                        "slowmode_seconds": config.automod_antiraid_slowmode_seconds,
                        "cooldown_seconds": config.automod_antiraid_cooldown_seconds,
                    },
                    "exempt_regex": config.automod_exempt_regex,
                    "lockdown_seconds": config.automod_lockdown_seconds,
                },
            }
        )

    async def _api_automod_post(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        service = self.services.settings
        updates: dict[str, Any] = {}
        if "enabled" in payload:
            updates["automod_enabled"] = 1 if payload.get("enabled") else 0
        if updates:
            await service.update(guild.id, **updates)
        if "words" in payload:
            raw = payload.get("words")
            words = raw if isinstance(raw, list) else re.split(r"[\n,]+", str(raw or ""))
            await service.set_blocked_words(guild.id, list(words))
        settings = await service.get(guild.id)
        return self._json(
            {
                "ok": True,
                "enabled": bool(self.bot.config.automod_enabled and settings.get("automod_enabled", True)),
                "db_enabled": bool(settings.get("automod_enabled", True)),
                "blocked_words": await service.blocked_words(guild.id),
            }
        )

    async def _api_automod_lockdown(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        cog = self.bot.get_cog("AutoMod")
        if cog is None or not hasattr(cog, "activate_lockdown"):
            return self._json({"ok": False, "error": "Ког AutoMod не загружен"}, status=503)
        payload = await self._read_json(request)
        try:
            seconds = max(30, min(int(payload.get("seconds") or self.bot.config.automod_lockdown_seconds), 3600))
        except (TypeError, ValueError):
            seconds = self.bot.config.automod_lockdown_seconds
        changed = await cog.activate_lockdown(guild, seconds)
        return self._json({"ok": True, "seconds": seconds, "channels": changed})

    # --- API: опросы ---

    async def _api_polls(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.polls
        rows = await service.list_for_guild(guild.id, 200)
        items: list[dict[str, Any]] = []
        for row in rows:
            options = json.loads(row["options"] or "[]")
            counts = await service.vote_counts(row["id"])
            items.append(
                {
                    "id": row["id"],
                    "channel_id": str(row["channel_id"]),
                    "channel_name": self._channel_name(guild, row["channel_id"]),
                    "author_name": self._member_name(guild, row["author_id"]),
                    "question": row["question"],
                    "options": options,
                    "counts": {str(k): v for k, v in counts.items()},
                    "total": sum(counts.values()),
                    "created_at": row["created_at"],
                    "active": bool(row["active"]),
                    "message_id": str(row["message_id"]) if row.get("message_id") else "",
                }
            )
        return self._json({"ok": True, "polls": items, "channels": self._channel_options()})

    async def _api_polls_create(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        channel = self._resolve_channel(payload.get("channel_id"))
        if channel is None or channel.guild.id != guild.id:
            return self._json({"ok": False, "error": "Канал не найден на этом сервере"}, status=400)
        question = str(payload.get("question") or "").strip()[:256]
        raw_options = payload.get("options") or []
        options = [str(opt).strip()[:100] for opt in raw_options if str(opt).strip() and str(opt).strip().lower() != "нет"]
        if not question:
            return self._json({"ok": False, "error": "Укажите вопрос опроса"}, status=400)
        if len(options) < 2:
            return self._json({"ok": False, "error": "Минимум 2 варианта ответа"}, status=400)
        options = options[:5]

        service = self.services.polls
        poll_id = await service.create(guild.id, channel.id, guild.me.id if guild.me else 0, question, options)
        embed = await service.embed(poll_id)
        from app.core.views import PollView

        view = PollView(poll_id, len(options))
        try:
            message = await channel.send(embed=embed, view=view)
        except (discord.HTTPException, discord.Forbidden) as exc:
            return self._json({"ok": False, "error": str(exc)}, status=400)
        await service.bind_message(poll_id, message.id)
        self.bot.add_view(view, message_id=message.id)
        return self._json({"ok": True, "poll_id": poll_id, "message_id": message.id, "channel_name": channel.name})

    async def _api_polls_end(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        poll_id = self._parse_id(request.match_info.get("poll_id"))
        if poll_id is None:
            return self._json({"ok": False, "error": "Неверный ID опроса"}, status=400)
        service = self.services.polls
        poll = await service.get(poll_id)
        if poll is None or poll["guild_id"] != guild.id:
            return self._json({"ok": False, "error": "Опрос не найден"}, status=404)
        if not poll["active"]:
            return self._json({"ok": True, "already": True})
        question, options, counts = await service.end(poll_id)
        result = embeds.info(f"📊 Итоги: {question}")
        total = sum(counts.values())
        for index, option in enumerate(options):
            votes = counts.get(index, 0)
            percent = round(votes / total * 100) if total else 0
            result.add_field(name=option, value=f"**{votes}** голосов ({percent}%)", inline=False)
        result.set_footer(text=f"ID опроса: {poll_id} • Всего голосов: {total}")
        if poll.get("message_id"):
            channel = self.bot.get_channel(poll["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(poll["message_id"])
                    await message.edit(embed=result, view=None)
                except discord.HTTPException:
                    pass
        return self._json({"ok": True, "question": question, "counts": {str(k): v for k, v in counts.items()}, "total": total})

    # --- API: дни рождения ---

    async def _api_birthdays_get(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.birthdays
        rows = await service.all()
        items = [
            {
                "user_id": str(row["user_id"]),
                "name": self._member_name(guild, row["user_id"]),
                "month": row["month"],
                "day": row["day"],
            }
            for row in rows
        ]
        settings = await self.services.settings.get(guild.id)
        return self._json(
            {
                "ok": True,
                "birthdays": items,
                "members": self._member_options(guild, 400),
                "channel_id": str(settings.get("birthday_channel_id") or self.bot.config.birthday_channel_id or ""),
            }
        )

    async def _api_birthdays_post(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        payload = await self._read_json(request)
        user_id = self._parse_id(payload.get("member_id"))
        if user_id is None or guild.get_member(user_id) is None:
            return self._json({"ok": False, "error": "Участник не найден на сервере"}, status=400)
        month = self._parse_id(payload.get("month"))
        day = self._parse_id(payload.get("day"))
        if month is None or day is None:
            return self._json({"ok": False, "error": "Некорректная дата: месяц 1–12, день 1–31"}, status=400)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return self._json({"ok": False, "error": "Некорректная дата: месяц 1–12, день 1–31"}, status=400)
        try:
            datetime(2000, month, day)
        except ValueError:
            return self._json({"ok": False, "error": "Некорректная дата"}, status=400)
        service = self.services.birthdays
        await service.set(user_id, month, day)
        return self._json({"ok": True, "name": self._member_name(guild, user_id)})

    async def _api_birthdays_remove(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        user_id = self._parse_id(request.match_info.get("user_id"))
        if user_id is None or not await self.services.birthdays.remove(user_id):
            return self._json({"ok": False, "error": "День рождения не найден"}, status=404)
        return self._json({"ok": True})

    # --- API: временные голосовые ---

    async def _api_tempvoice_get(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        service = self.services.tempvoice
        rooms: list[dict[str, Any]] = []
        for row in await service.all():
            channel = guild.get_channel(row["channel_id"])
            rooms.append(
                {
                    "channel_id": str(row["channel_id"]),
                    "name": channel.name if channel else None,
                    "owner_id": str(row["owner_id"]),
                    "owner_name": self._member_name(guild, row["owner_id"]),
                    "created_at": row["created_at"],
                    "members": [
                        {
                            "id": str(member.id),
                            "name": member.display_name,
                            "bot": member.bot,
                        }
                        for member in (channel.members if isinstance(channel, discord.VoiceChannel) else [])
                    ],
                }
            )
        config = self.bot.config
        triggers = [{"id": str(cid), "name": self._channel_name(guild, cid)} for cid in config.temp_voice_trigger_ids]
        category = guild.get_channel(config.temp_voice_category_id) if config.temp_voice_category_id else None
        return self._json(
            {
                "ok": True,
                "rooms": rooms,
                "triggers": triggers,
                "category_id": str(config.temp_voice_category_id) if config.temp_voice_category_id else "",
                "category_name": category.name if category else None,
                "members": self._member_options(guild, 400),
            }
        )

    async def _api_tempvoice_delete(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        channel_id = self._parse_id(request.match_info.get("channel_id"))
        if channel_id is None:
            return self._json({"ok": False, "error": "Неверный ID канала"}, status=400)
        service = self.services.tempvoice
        if not await service.owner_of(channel_id):
            return self._json({"ok": False, "error": "Комната не найдена"}, status=404)
        await service.delete(channel_id)
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.VoiceChannel):
            try:
                await channel.delete(reason="Удаление комнаты из веб-панели")
            except (discord.HTTPException, discord.Forbidden):
                pass
        return self._json({"ok": True})

    async def _api_tempvoice_transfer(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        channel_id = self._parse_id(request.match_info.get("channel_id"))
        payload = await self._read_json(request)
        new_owner_id = self._parse_id(payload.get("owner_id"))
        if channel_id is None or new_owner_id is None:
            return self._json({"ok": False, "error": "Неверные параметры"}, status=400)
        service = self.services.tempvoice
        if not await service.owner_of(channel_id):
            return self._json({"ok": False, "error": "Комната не найдена"}, status=404)
        if guild.get_member(new_owner_id) is None:
            return self._json({"ok": False, "error": "Новый владелец не найден на сервере"}, status=400)
        await service.transfer(channel_id, new_owner_id)
        return self._json({"ok": True, "owner_name": self._member_name(guild, new_owner_id)})

    async def _api_schedule(self, request: web.Request) -> web.Response:
        service = self.services.scheduled
        rows = await service.recent(200)
        guild = self._primary_guild()
        upcoming: list[dict[str, Any]] = []
        done: list[dict[str, Any]] = []
        for row in rows:
            embed_schema: dict[str, Any] = {}
            try:
                embed_schema = json.loads(row.get("embed_json") or "{}") or {}
            except (TypeError, ValueError):
                embed_schema = {}
            item = {
                "id": row["id"],
                "guild_id": str(row["guild_id"]),
                "channel_id": str(row["channel_id"]),
                "channel_name": (self._channel_name(guild, row["channel_id"]) if guild is not None else str(row["channel_id"])),
                "content": (row["content"] or "")[:120],
                "title": (embed_schema.get("title") or "")[:120],
                "send_at": row["send_at"],
                "created_at": row["created_at"],
                "done": bool(row["done"]),
            }
            (done if row["done"] else upcoming).append(item)
        upcoming.sort(key=lambda item: item["send_at"])
        # Актуальный канал ищем по всем гильдиям бота, не только primary.
        for item in upcoming:
            ch = self.bot.get_channel(int(item["channel_id"]))
            if ch is not None:
                item["channel_name"] = f"{'🔊' if isinstance(ch, discord.VoiceChannel) else '#'} {ch.name}"
        return self._json({"ok": True, "upcoming": upcoming, "done": done})

    async def _api_schedule_create(self, request: web.Request) -> web.Response:
        payload = await self._read_json(request)
        channel = self._resolve_channel(payload.get("channel_id") or "")
        if channel is None or not isinstance(channel, discord.TextChannel):
            return self._json({"ok": False, "error": "Канал не найден"}, status=400)
        send_at = None
        raw_at = str(payload.get("send_at") or "")
        if raw_at:
            try:
                send_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
            except ValueError:
                return self._json({"ok": False, "error": "Некорректная дата отправки"}, status=400)
        if send_at is None:
            return self._json({"ok": False, "error": "Укажите дату отправки"}, status=400)
        content = str(payload.get("content") or "").strip()
        embed = payload.get("embed")
        embed = embed if isinstance(embed, dict) else {}
        if not content and not (embed.get("title") or embed.get("description")):
            return self._json({"ok": False, "error": "Укажите текст или эмбед"}, status=400)
        service = self.services.scheduled
        try:
            scheduled_id = await service.create(
                channel.guild.id, channel.id, self.bot.user.id, send_at, content=content[:2000], embed=embed
            )
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, status=400)
        return self._json({"ok": True, "id": scheduled_id, "channel": channel.name, "send_at": send_at.isoformat()})

    async def _api_schedule_delete(self, request: web.Request) -> web.Response:
        message_id = self._parse_id(request.match_info.get("schedule_id"))
        if message_id is None:
            return self._json({"ok": False, "error": "Неверный ID"}, status=400)
        service = self.services.scheduled
        deleted = await service.delete(message_id)
        if not deleted:
            return self._json({"ok": False, "error": "Запись не найдена"}, status=404)
        return self._json({"ok": True, "id": message_id})

    async def _api_backup(self, request: web.Request) -> web.Response:
        guild = self._primary_guild()
        if guild is None:
            return self._json({"ok": False, "error": "Бот не подключён ни к одному серверу"}, status=400)
        settings_service = self.services.settings
        settings = await settings_service.get(guild.id)
        moderation = self.services.moderation
        giveaways = self.services.giveaways
        scheduled = self.services.scheduled
        data = {
            "generated_at": datetime.now(UTC).isoformat(),
            "bot": {
                "name": self.bot.user.name if self.bot.user else None,
                "guild_id": str(guild.id),
                "guild_name": guild.name,
            },
            "modules": self._modules_status(),
            "settings": dict(settings),
            "blocked_words": await settings_service.blocked_words(guild.id),
            "warns": await moderation.all_warns(guild.id, 2000),
            "giveaways": await giveaways.recent_for_guild(guild.id, 200),
            "scheduled": await scheduled.recent(200),
        }
        return self._attachment(json_data=data, filename=f"backup-{guild.name}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json")

    async def _api_backup_db(self, request: web.Request) -> web.Response:
        db_path = Path(self.bot.config.db_path)
        if not db_path.is_file():
            return self._json({"ok": False, "error": "Файл БД не найден"}, status=404)
        snapshot_path = db_path.with_suffix(f".snapshot-{int(time.time())}.db")
        try:
            if self.bot.db is None:
                return self._json({"ok": False, "error": "База данных не подключена"}, status=503)
            await self.bot.db.backup(snapshot_path)
            if not snapshot_path.is_file():
                return self._json({"ok": False, "error": "Не удалось создать снапшот"}, status=500)
            body = snapshot_path.read_bytes()
            snapshot_path.unlink(missing_ok=True)
            return web.Response(
                body=body,
                content_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="db-snapshot-{datetime.now(UTC).strftime("%Y%m%d-%H%M%S")}.db"'},
            )
        except Exception as exc:
            snapshot_path.unlink(missing_ok=True)
            return self._json({"ok": False, "error": f"Ошибка снапшота: {exc}"[:200]}, status=500)

    def _attachment(self, *, data: bytes | None = None, json_data: dict[str, Any] | None = None, filename: str) -> web.Response:
        if json_data is not None:
            data = json.dumps(json_data, ensure_ascii=False, indent=2).encode("utf-8")
        # Guild names and other user-controlled labels must not be copied
        # directly into a response header.
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "download"
        return web.Response(
            body=data,
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"', "Cache-Control": "no-store"},
        )

    # --- API: прокси вебхуков ---

    @staticmethod
    def _parse_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _webhook_base(self, url: Any) -> str | None:
        match = _WEBHOOK_RE.match(str(url or ""))
        if match is None:
            return None
        return f"https://discord.com/api/webhooks/{match.group(1)}/{match.group(2)}"

    async def _read_json(self, request: web.Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _webhook_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "content": str(payload.get("content") or "")[:2000] or "",
            "embeds": _trim_embeds(payload.get("embeds")),
            "allowed_mentions": {"parse": []},
        }
        components = _trim_components(payload.get("components"))
        if components:
            body["components"] = components
        return body

    async def _api_webhook_send(self, request: web.Request) -> web.Response:
        payload = await self._read_json(request)
        base = self._webhook_base(payload.get("webhook_url"))
        if base is None:
            return self._json({"ok": False, "error": "Неверный Webhook URL"}, status=400)
        try:
            async with self._require_http().post(f"{base}?wait=true", json=self._webhook_body(payload), allow_redirects=False) as response:
                data = await self._read_remote_json(response)
                if response.status >= 400:
                    return self._json(
                        {"ok": False, "error": data.get("message") or str(response.status), "data": data},
                        status=response.status,
                    )
                return self._json({"ok": True, "data": {"id": str(data.get("id"))}})
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("Не удалось отправить сообщение через webhook", exc_info=True)
            return self._json({"ok": False, "error": "Discord Webhook недоступен"}, status=502)

    async def _api_webhook_edit(self, request: web.Request) -> web.Response:
        payload = await self._read_json(request)
        base = self._webhook_base(payload.get("webhook_url"))
        message_id = self._parse_id(payload.get("message_id"))
        if base is None or message_id is None:
            return self._json({"ok": False, "error": "Неверный Webhook URL или ID сообщения"}, status=400)
        try:
            async with self._require_http().patch(
                f"{base}/messages/{message_id}", json=self._webhook_body(payload), allow_redirects=False
            ) as response:
                data = await self._read_remote_json(response)
                if response.status >= 400:
                    return self._json(
                        {"ok": False, "error": data.get("message") or str(response.status), "data": data},
                        status=response.status,
                    )
                return self._json({"ok": True, "data": {"id": str(data.get("id"))}})
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("Не удалось изменить сообщение через webhook", exc_info=True)
            return self._json({"ok": False, "error": "Discord Webhook недоступен"}, status=502)

    async def _api_webhook_fetch(self, request: web.Request) -> web.Response:
        payload = await self._read_json(request)
        base = self._webhook_base(payload.get("webhook_url"))
        message_id = self._parse_id(payload.get("message_id"))
        if base is None or message_id is None:
            return self._json({"ok": False, "error": "Неверный Webhook URL или ID сообщения"}, status=400)
        try:
            async with self._require_http().get(f"{base}/messages/{message_id}", allow_redirects=False) as response:
                data = await self._read_remote_json(response)
                if response.status >= 400:
                    return self._json(
                        {"ok": False, "error": data.get("message") or str(response.status), "data": data},
                        status=response.status,
                    )
                return self._json({"ok": True, "data": _raw_to_client(data)})
        except (aiohttp.ClientError, TimeoutError):
            logger.warning("Не удалось получить сообщение через webhook", exc_info=True)
            return self._json({"ok": False, "error": "Discord Webhook недоступен"}, status=502)

    async def _api_logs(self, request: web.Request) -> web.Response:
        limit = request.query.get("n", "200")
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 200
        level = request.query.get("level", "")
        cat = request.query.get("cat", "")
        audit_raw = request.query.get("audit", "").lower()
        audit = "1" if audit_raw in ("1", "true", "yes") else ("0" if audit_raw in ("0", "false", "no") else "")
        entries = self._ring.snapshot(limit, level=level, cat=cat, audit=audit) if self._ring is not None else []
        return self._json({"ok": True, "count": len(entries), "logs": entries})

    async def _api_admin_audit(self, request: web.Request) -> web.Response:
        if self.bot.db is None:
            return self._json({"ok": False, "error": "База данных недоступна"}, status=503)
        raw_limit = request.query.get("n", "200")
        try:
            limit = max(1, min(int(raw_limit), 1000))
        except (TypeError, ValueError):
            limit = 200
        return self._json({"ok": True, "audit": await self.bot.db.list_admin_audit(limit)})

    @staticmethod
    async def _read_remote_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Read Discord responses even when a proxy returns non-JSON content."""
        try:
            data = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            text = await response.text()
            return {"message": text[:200]}
        return data if isinstance(data, dict) else {"data": data}

    def _require_http(self) -> aiohttp.ClientSession:
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=5))
        return self._http
