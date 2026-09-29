"""Сервис карточки «Где играем».

Перенос ``app/where_play.py`` из beda-discord-bot в архитектуру сервисов.
Что изменилось по сравнению с оригиналом:

* HTTP идёт через общий :class:`ApiClient` (таймауты, retry, circuit breaker,
  единый User-Agent) вместо собственной сессии aiohttp;
* код подключения нормализуется и валидируется без regex-ловушек: допустимы
  только числа community-сервера и UUID, длина ограничена настройками;
* значения из стороннего API экранируются и ограничиваются по длине перед
  попаданием в эмбед (``@`` тоже экранируется, чтобы удалённый сервер не
  разослал пинг от имени бота);
* карточка и очередь коллеров хранятся в БД, поэтому переживают рестарт.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import discord

from app.core import embeds
from app.core.api_client import ApiClient, ApiRequestError
from app.core.base import BaseService
from app.core.views import WherePlayCallerView
from app.utils.format import truncate

if TYPE_CHECKING:
    from app.core.bot import MegaBot
    from app.db.where_play_repository import WherePlayRepository
    from app.types import WherePlayRow

logger = logging.getLogger("bot.where_play")

TEAMS: tuple[str, ...] = ("🔵 Синие", "🔴 Красные", "🟢 Зелёные")

_EPOCH = datetime.fromtimestamp(0, UTC)
_NUMERIC_CODE = re.compile(r"^\d{1,32}$")
_UUID_CODE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class WherePlayError(ValueError):
    """Ошибка сценария «где играем», показываемая пользователю."""


def normalize_join_code(value: str, *, minimum: int = 8, maximum: int = 36) -> str:
    """Приводит код подключения к каноническому виду.

    Допустимы только числа community-сервера и UUID: название сервера, которое
    обычно ищут в интерфейсе, кодом не является.
    """
    text = " ".join(value.split())
    if not minimum <= len(text) <= maximum:
        raise WherePlayError("Нужен код подключения из игры: число или UUID community-сервера.")
    if _NUMERIC_CODE.match(text):
        return text
    lowered = text.lower()
    if _UUID_CODE.match(lowered):
        return lowered
    raise WherePlayError("Нужен код подключения из игры: число или UUID community-сервера.")


def find_server(snapshot: dict[str, Any], code: str) -> dict[str, Any] | None:
    """Ищет сервер по коду подключения; дубликаты считаются ошибкой данных."""
    target = code.lower()
    matches = [entry for entry in snapshot.get("data", []) if str(entry.get("serverId", "")).lower() == target]
    if len(matches) > 1:
        raise WherePlayError("API вернул несколько серверов с этим кодом. Повторите позже.")
    return matches[0] if matches else None


def is_stale(snapshot: dict[str, Any], now: datetime | None = None) -> bool:
    """Помечает снапшот как устаревший по полю ``meta.stale`` и времени выборки."""
    meta = snapshot.get("meta")
    if not isinstance(meta, dict):
        return True
    if meta.get("stale", True):
        return True
    raw = meta.get("fetchedAt")
    if not raw:
        return True
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    moment = now or datetime.now(UTC)
    try:
        refresh = float(meta["refreshSeconds"])
    except (KeyError, TypeError, ValueError):
        return True
    return (moment - stamp).total_seconds() > refresh + 60


def safe_field_text(value: object, maximum: int = 200) -> str:
    """Экранирует и ограничивает недоверенный текст из внешнего API."""
    if value is None or isinstance(value, (dict, list, bool)):
        return "—"
    text = " ".join(str(value).split())
    if not text:
        return "—"
    escaped = discord.utils.escape_markdown(truncate(text, maximum))
    return escaped.replace("@", r"\@")


def safe_number(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "—"
    text = str(value).strip()
    return text[:20] if text else "—"


class ServerSnapshot:
    """Кэш снапшота серверов с ETag и уважением интервала обновления."""

    def __init__(self, client: ApiClient, url: str, refresh_hint: float = 60.0) -> None:
        self._client = client
        self._url = url
        self._refresh_hint = refresh_hint
        self._snapshot: dict[str, Any] | None = None
        self._etag: str | None = None
        self._next_request = 0.0
        self._next_allowed = 0.0
        self.last_success: datetime | None = None
        self.last_error: str | None = None

    @property
    def snapshot(self) -> dict[str, Any] | None:
        return self._snapshot

    def _refresh_of(self, payload: dict[str, Any]) -> float:
        try:
            return max(60.0, float(payload["meta"]["refreshSeconds"]))
        except (KeyError, TypeError, ValueError):
            return self._refresh_hint

    async def get(self, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        monotonic = time.monotonic()
        if monotonic < self._next_allowed and self._snapshot is not None:
            if self.last_error:
                raise WherePlayError(self.last_error)
            return self._snapshot
        if monotonic < self._next_request and self._snapshot is not None:
            return self._snapshot
        headers = {"If-None-Match": self._etag} if self._etag else {}
        try:
            status, payload, response_headers = await self._client.json(
                "GET", self._url, attempts=2, acceptable=(200, 304), headers=headers
            )
        except ApiRequestError as exc:
            self.last_error = f"WardogServers недоступен ({exc.status}). Карточка показывает последние данные."
            self._next_allowed = monotonic + 60
            if self._snapshot is not None:
                return self._snapshot
            raise WherePlayError(self.last_error) from exc

        if status == 304 and self._snapshot is not None:
            self._next_request = monotonic + self._refresh_of(self._snapshot)
            return self._snapshot
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not isinstance(payload.get("meta"), dict):
            self.last_error = "WardogServers вернул неподдерживаемый формат данных."
            if self._snapshot is not None:
                return self._snapshot
            raise WherePlayError(self.last_error)

        etag = response_headers.get("ETag") if response_headers else None
        self._etag = etag or self._etag
        self._snapshot = payload
        self.last_success = moment
        self.last_error = None
        self._next_request = monotonic + self._refresh_of(payload)
        self._next_allowed = 0.0
        return payload


class WherePlayService(BaseService["WherePlayRepository"]):
    repo: WherePlayRepository

    def __init__(self, repo: WherePlayRepository, bot: MegaBot) -> None:
        super().__init__(repo)
        self.bot = bot
        self._api = ApiClient(
            "wardogservers",
            timeout=bot.config.api_timeout_seconds,
            user_agent="ClanBot/3.3 (+https://discord.com)",
            proxy=bot.config.api_proxy,
            max_concurrency=bot.config.api_max_concurrency,
            circuit_failure_threshold=bot.config.api_circuit_failure_threshold,
            circuit_reset_seconds=bot.config.api_circuit_reset_seconds,
        )
        self._snapshot = ServerSnapshot(self._api, bot.config.where_play_api_url)

    async def close(self) -> None:
        await self._api.close()

    # --- хранилище ---

    async def row(self, guild_id: int) -> WherePlayRow | None:
        return await self._repo.get(guild_id)

    async def configure_channel(self, guild_id: int, channel_id: int, role_id: int | None) -> WherePlayRow | None:
        await self._repo.upsert_channel(guild_id, channel_id, role_id)
        return await self._repo.get(guild_id)

    async def bind_message(self, guild_id: int, message_id: int) -> None:
        await self._repo.set_message_id(guild_id, message_id)

    async def select_server(self, guild_id: int, code: str, team: str, caller_id: int) -> WherePlayRow | None:
        """Проверяет код по свежему снапшоту и сохраняет выбор."""
        normalized = normalize_join_code(code, minimum=self.bot.config.join_code_min, maximum=self.bot.config.join_code_max)
        if team not in TEAMS:
            raise WherePlayError("Выберите команду из списка: 🔵 Синие, 🔴 Красные или 🟢 Зелёные.")
        snapshot = await self._snapshot.get()
        if is_stale(snapshot):
            raise WherePlayError("Данные API устарели. Текущая карточка сохранена; повторите позже.")
        selected = find_server(snapshot, normalized)
        if not selected:
            raise WherePlayError(
                "Код не найден в актуальном списке. Проверьте код подключения из игры и повторите позже. Текущий сервер не изменён."
            )
        payload = json.dumps({"server": selected, "fetchedAt": snapshot["meta"].get("fetchedAt")}, ensure_ascii=False)
        await self._repo.set_active(
            guild_id,
            code=normalized,
            team=team,
            caller_id=caller_id,
            payload=payload,
            fetched_at=datetime.now(UTC),
        )
        return await self._repo.get(guild_id)

    async def stop(self, guild_id: int) -> WherePlayRow | None:
        row = await self._repo.get(guild_id)
        if row is None:
            return None
        await self._repo.set_active(
            guild_id,
            code=str(row["code"]),
            team=str(row["team"]),
            caller_id=int(row["caller_id"] or 0),
            payload=str(row["payload"]),
            fetched_at=_parse_iso(row["fetched_at"]) or datetime.now(UTC),
            active=False,
        )
        return await self._repo.get(guild_id)

    def is_manager(self, row: WherePlayRow, user: discord.abc.GuildUser) -> bool:
        """Администратор или участник роли коллеров управляет карточкой."""
        if user.guild_permissions.administrator:
            return True
        role_id = row.get("role_id")
        return role_id is not None and any(role.id == role_id for role in user.roles)

    # --- карточка ---

    def card(self, row: WherePlayRow, warning: str | None = None, caller_lines: list[str] | None = None) -> discord.Embed:
        embed = embeds.brand("Где играем", "Сейчас общего сбора нет.")
        embed.set_footer(text="Данные: WardogServers • независимый сервис")
        if not row["active"]:
            embed.add_field(name="Источник", value="[WardogServers](https://wardogservers.com)", inline=False)
            return embed

        server = _server_of(row)
        embed.description = warning or "Скопируйте код подключения, найдите сервер в игре и выберите нашу команду."
        if warning:
            embed.color = embeds.WARNING
        embed.add_field(name="СЕРВЕР", value=safe_field_text(server.get("name")), inline=False)
        embed.add_field(name="КОД", value=truncate(f"`{row['code'] or '—'}`", 100), inline=True)
        embed.add_field(name="РЕГИОН", value=safe_field_text(server.get("region")), inline=True)
        mapping = server.get("map") if isinstance(server.get("map"), dict) else {}
        embed.add_field(name="КАРТА", value=safe_field_text(mapping.get("variant")), inline=True)
        embed.add_field(
            name="ИГРОКИ" + (" (последние данные)" if warning else ""),
            value=f"{safe_number(server.get('players'))} / {safe_number(server.get('maxPlayers'))}",
            inline=True,
        )
        embed.add_field(name="НАША КОМАНДА", value=truncate(str(row["team"] or "—"), 100), inline=True)
        if row["caller_id"]:
            embed.add_field(name="СБОР ОБЪЯВИЛ", value=f"<@{row['caller_id']}>", inline=False)
        fetched_at = _parse_iso(row["fetched_at"])
        if fetched_at:
            embed.add_field(
                name="ДАННЫЕ ОБНОВЛЕНЫ",
                value=f"<t:{int(fetched_at.timestamp())}:f> · <t:{int(fetched_at.timestamp())}:R>",
                inline=False,
            )
        embed.add_field(
            name="КОЛЛЕРЫ",
            value="\n".join(caller_lines) if caller_lines else "Пока нет. Нажмите «Я коллер», находясь в голосовом канале.",
            inline=False,
        )
        embed.add_field(
            name="ИСТОЧНИК",
            value="[WardogServers](https://wardogservers.com) — независимый сервис",
            inline=False,
        )
        return embed

    async def caller_lines(self, row: WherePlayRow, limit: int = 10) -> list[str]:
        """Строки «участник — комната» по активным комнатам коллеров."""
        guild = self.bot.get_guild(int(row["guild_id"]))
        if guild is None:
            return []
        lines: list[str] = []
        total = 0
        for record in await self._repo.caller_rooms(guild.id):
            channel = guild.get_channel(int(record["channel_id"]))
            if not isinstance(channel, discord.VoiceChannel):
                await self._repo.forget_caller_room(guild.id, int(record["member_id"]))
                continue
            if not any(member.id == record["member_id"] for member in channel.members):
                await self._repo.forget_caller_room(guild.id, int(record["member_id"]))
                continue
            total += 1
            if len(lines) >= limit:
                continue
            lines.append(f"<@{record['member_id']}> — <#{channel.id}>")
        if total > limit:
            lines.append(f"…и ещё {total - limit}")
        return lines

    async def caller_slots(self, guild: discord.Guild) -> tuple[set[int], int]:
        owners: set[int] = set()
        for record in await self._repo.caller_rooms(guild.id):
            channel = guild.get_channel(int(record["channel_id"]))
            if isinstance(channel, discord.VoiceChannel):
                owners.add(int(record["member_id"]))
        return owners, len(owners)

    async def remember_caller_room(self, guild_id: int, member_id: int, channel_id: int) -> None:
        await self._repo.remember_caller_room(guild_id, member_id, channel_id, datetime.now(UTC))

    async def forget_caller_room(self, guild_id: int, member_id: int) -> None:
        await self._repo.forget_caller_room(guild_id, member_id)

    # --- публикация и обновление ---

    async def publish(self, row: WherePlayRow, warning: str | None = None) -> None:
        embed = self.card(row, warning, await self.caller_lines(row))
        channel = self.bot.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        if row["message_id"]:
            try:
                await channel.get_partial_message(int(row["message_id"])).edit(
                    embed=embed, view=WherePlayCallerView(), allowed_mentions=discord.AllowedMentions.none()
                )
                return
            except discord.NotFound:
                pass
        message = await channel.send(embed=embed, view=WherePlayCallerView(), allowed_mentions=discord.AllowedMentions.none())
        await self._repo.set_message_id(int(row["guild_id"]), message.id)

    async def refresh(self) -> int:
        """Обновляет карточки всех активных сборов; возвращает число ошибок."""
        rows = await self._repo.active_guilds()
        if not rows:
            return 0
        now = datetime.now(UTC)
        warning: str | None = None
        snapshot: dict[str, Any] | None = None
        try:
            snapshot = await self._snapshot.get(now)
            if is_stale(snapshot, now):
                warning = "⚠️ Источник передаёт устаревшие данные."
        except WherePlayError as exc:
            warning = f"⚠️ {exc}\nНиже последние известные данные."

        failures = 0
        for row in rows:
            guild_id = int(row["guild_id"])
            current = warning
            try:
                if snapshot is not None:
                    server = find_server(snapshot, str(row["code"] or ""))
                    if server is not None:
                        fresh_at = _parse_iso(snapshot["meta"].get("fetchedAt")) or now
                        if fresh_at >= (_parse_iso(row["fetched_at"]) or _EPOCH):
                            await self._repo.store_payload(
                                guild_id,
                                json.dumps(
                                    {"server": server, "fetchedAt": snapshot["meta"].get("fetchedAt")},
                                    ensure_ascii=False,
                                ),
                                fresh_at,
                            )
                            row = await self._repo.get(guild_id) or row
                    elif not warning:
                        current = "⚠️ Сервер отсутствует в свежем списке. Ниже последние известные данные; другой сервер не выбран."
                await self.publish(row, current)
            except Exception:
                failures += 1
                logger.exception("Не удалось обновить карточку «Где играем» для сервера %s", guild_id)
        return failures


def _server_of(row: WherePlayRow) -> dict[str, Any]:
    try:
        data = json.loads(row["payload"]) if row["payload"] else {}
    except (TypeError, ValueError):
        return {}
    server = data.get("server") if isinstance(data, dict) else None
    return server if isinstance(server, dict) else {}


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
