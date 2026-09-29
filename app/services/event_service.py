"""Сервис ивентов: мастер создания, записи участия, карточка и напоминания.

Перенос VacationBot ``cogs/events.py`` в архитектуру сервисов. Отличия от
оригинала, которые здесь исправлены:

* состояние мастера живёт в :class:`EventFlowStore` (с TTL) вместо глобальных
  словарей модуля, поэтому два параллельных мастера не путают шаги;
* ID ивента берётся из БД, а не вытаскивается парсингом footer эмбеда;
* время хранится в ISO 8601 (UTC) вместо строк «ДД.ММ.ГГГГ ЧЧ:ММ», поэтому
  напоминания считаются по таймзоне, а не по времени сервера;
* проверяется порядок «сбор ≤ начало», а прошедшие даты отклоняются;
* флаги напоминаний обновляются в БД, повторных пингов после рестарта нет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord

from app.core import embeds
from app.core.base import BaseService
from app.core.views import EVENT_SIGNUP_OPTIONS
from app.db.events_repository import SIGNUP_ROLES
from app.types import EventRow
from app.utils.format import truncate

if TYPE_CHECKING:
    from app.db.events_repository import EventsRepository

#: Цвета типа события.
EVENT_TYPES: dict[str, tuple[str, str, discord.Color]] = {
    "competitive": ("Компетитив", "⚔️", discord.Color(0xE74C3C)),
    "freeform": ("Свободная форма", "🧩", discord.Color(0x6C7CFF)),
}

EVENT_FIELDS = ("name", "description", "briefing_at", "start_at", "image_url")
FIELD_LABELS = {
    "name": "Название",
    "description": "Описание",
    "briefing_at": "Время сбора",
    "start_at": "Время начала",
    "image_url": "Изображение",
}

_DM_DATETIME = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
MAX_NAME = 120
MAX_DESCRIPTION = 1000
MAX_IMAGE_URL = 300
FLOW_TTL = timedelta(hours=2)


class EventValidationError(ValueError):
    """Некорректные данные ивента, заданные пользователем."""


def parse_event_datetime(raw: str) -> datetime:
    """Парсит «ДД.ММ.ГГГГ ЧЧ:ММ» или ISO-дату в aware-datetime UTC.

    Время в мастере вводится в московской зоне (UTC+3, без перехода на
    летнее время), поэтому локальное время пользователя не влияет на расчёт
    напоминаний.
    """
    text = raw.strip()
    if not text:
        raise EventValidationError("Введите дату и время, например `25.04.2026 19:00`.")
    match = _DM_DATETIME.match(text)
    if match:
        day, month, year, hour, minute = (int(part) for part in match.groups())
        try:
            # Ввод считается московским: UTC = локальное − 3 часа.
            local = datetime(year, month, day, hour, minute, tzinfo=UTC) - timedelta(hours=3)
        except ValueError as exc:
            raise EventValidationError("Такой даты не существует. Проверьте день, месяц и год.") from exc
        return local
    if _ISO_DATETIME.match(text):
        try:
            parsed = datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError as exc:
            raise EventValidationError("Не удалось разобрать дату. Формат: `25.04.2026 19:00`.") from exc
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise EventValidationError("Формат даты: `ДД.ММ.ГГГГ ЧЧ:ММ`, например `25.04.2026 19:00`.")


def format_event_datetime(value: datetime) -> str:
    """Отображает дату ивента в московской зоне."""
    local = value.astimezone(UTC) + timedelta(hours=3)
    return local.strftime("%d.%m.%Y %H:%M")


def clean_name(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        raise EventValidationError("Название не может быть пустым.")
    if len(text) > MAX_NAME:
        raise EventValidationError(f"Название длиннее {MAX_NAME} символов.")
    return text


def clean_description(value: str) -> str:
    text = value.strip()
    if text == "-":
        return ""
    if len(text) > MAX_DESCRIPTION:
        raise EventValidationError(f"Описание длиннее {MAX_DESCRIPTION} символов.")
    return text


def clean_image_url(value: str) -> str:
    text = value.strip()
    if not text or text == "-":
        return ""
    if len(text) > MAX_IMAGE_URL:
        raise EventValidationError(f"Ссылка на изображение длиннее {MAX_IMAGE_URL} символов.")
    if not text.startswith(("http://", "https://")):
        raise EventValidationError("Нужна прямая ссылка на изображение (http/https) или `-`, чтобы убрать её.")
    return text


def validate_schedule(briefing_at: datetime, start_at: datetime, now: datetime | None = None) -> None:
    moment = now or datetime.now(UTC)
    if briefing_at >= start_at:
        raise EventValidationError("Время сбора должно быть раньше времени начала.")
    if briefing_at <= moment:
        raise EventValidationError("Время сбора уже прошло. Укажите будущее время.")


@dataclass(slots=True)
class EventFlow:
    """Состояние мастера создания/редактирования ивента."""

    user_id: int
    guild_id: int
    channel_id: int
    step: str
    event_id: int | None = None
    edit_field: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    touched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def touch(self) -> None:
        self.touched_at = datetime.now(UTC)

    def expired(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        return moment - self.touched_at > FLOW_TTL


class EventFlowStore:
    """Потокобезопасное хранилище мастеров: один активный мастер на пользователя."""

    def __init__(self) -> None:
        self._flows: dict[int, EventFlow] = {}
        self._tokens: dict[int, EventFlow] = {}
        self._next_token = 1

    def begin(self, flow: EventFlow) -> EventFlow:
        self._purge()
        previous = self._flows.get(flow.user_id)
        if previous is not None and previous is not flow:
            # Старые кнопки брошенного мастра больше не должны влиять на новый.
            self._tokens = {token: item for token, item in self._tokens.items() if item is not previous}
        self._flows[flow.user_id] = flow
        return flow

    def get(self, user_id: int) -> EventFlow | None:
        flow = self._flows.get(user_id)
        if flow is not None and flow.expired():
            self.discard(user_id)
            return None
        return flow

    def get_by_token(self, user_id: int, token: int) -> EventFlow | None:
        flow = self._tokens.get(token)
        if flow is None or flow.user_id != user_id or flow.expired():
            return None
        return flow

    def token_for(self, flow: EventFlow) -> int:
        token = self._next_token
        self._next_token += 1
        self._tokens[token] = flow
        return token

    def discard(self, user_id: int) -> None:
        flow = self._flows.pop(user_id, None)
        if flow is not None:
            self._tokens = {token: item for token, item in self._tokens.items() if item is not flow}

    def discard_token(self, token: int) -> None:
        flow = self._tokens.pop(token, None)
        if flow is not None and self._flows.get(flow.user_id) is flow:
            self._flows.pop(flow.user_id, None)

    def _purge(self) -> None:
        stale = [user_id for user_id, flow in self._flows.items() if flow.expired()]
        for user_id in stale:
            self.discard(user_id)


class EventService(BaseService["EventsRepository"]):
    repo: EventsRepository

    def __init__(self, repo: EventsRepository, reminder_lead_minutes: int = 15) -> None:
        super().__init__(repo)
        self.reminder_lead_minutes = max(1, min(180, reminder_lead_minutes))
        self.flows = EventFlowStore()

    # --- создание и чтение ---

    async def create(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        name: str,
        event_type: str,
        description: str,
        briefing_at: datetime,
        start_at: datetime,
        image_url: str,
        show_not_going: bool,
    ) -> int:
        if event_type not in EVENT_TYPES:
            raise EventValidationError("Неизвестный тип события.")
        now = datetime.now(UTC)
        validate_schedule(briefing_at, start_at, now)
        return await self._repo.create(
            guild_id=guild_id,
            channel_id=channel_id,
            name=clean_name(name),
            event_type=event_type,
            description=clean_description(description),
            briefing_at=briefing_at,
            start_at=start_at,
            image_url=clean_image_url(image_url),
            show_not_going=show_not_going,
            creator_id=creator_id,
            created_at=now,
        )

    async def get(self, event_id: int) -> EventRow | None:
        return await self._repo.get(event_id)

    async def get_by_message(self, message_id: int) -> EventRow | None:
        return await self._repo.get_by_message(message_id)

    async def active_with_message(self) -> list[EventRow]:
        return await self._repo.active_with_message()

    async def recent_for_guild(self, guild_id: int, limit: int = 25) -> list[EventRow]:
        return await self._repo.recent_for_guild(guild_id, limit)

    async def bind_message(self, event_id: int, message_id: int) -> None:
        await self._repo.set_message_id(event_id, message_id)

    async def is_creator(self, event: EventRow, user_id: int) -> bool:
        return int(event["creator_id"]) == user_id

    # --- участие ---

    async def set_signup(self, event: EventRow, user_id: int, role: str) -> bool:
        """Возвращает True, если статус установлен, False — если снят."""
        if not event["active"]:
            raise EventValidationError("Ивент отменён.")
        if role not in SIGNUP_ROLES:
            raise EventValidationError("Неизвестный вариант участия.")
        return await self._repo.set_signup(int(event["id"]), user_id, role, datetime.now(UTC))

    async def signup_counts(self, event_id: int) -> dict[str, list[int]]:
        return await self._repo.signup_counts(event_id)

    async def participants(self, event_id: int) -> list[int]:
        return await self._repo.participants(event_id)

    # --- редактирование и отмена ---

    async def update(self, event: EventRow, field_name: str, value: str) -> None:
        if field_name not in EVENT_FIELDS:
            raise EventValidationError(f"Поле не редактируется: {field_name}")
        if not event["active"]:
            raise EventValidationError("Ивент отменён.")
        if field_name == "name":
            stored = clean_name(value)
        elif field_name == "description":
            stored = clean_description(value)
        elif field_name == "image_url":
            stored = clean_image_url(value)
        else:
            moment = parse_event_datetime(value)
            briefing = moment if field_name == "briefing_at" else datetime.fromisoformat(event["briefing_at"])
            start = moment if field_name == "start_at" else datetime.fromisoformat(event["start_at"])
            validate_schedule(briefing, start)
            stored = moment.isoformat()
        await self._repo.update_fields(int(event["id"]), field_name, stored)

    async def cancel(self, event_id: int) -> None:
        await self._repo.cancel(event_id)

    # --- напоминания ---

    async def due_reminders(self, now: datetime | None = None) -> list[EventRow]:
        moment = now or datetime.now(UTC)
        lead = moment + timedelta(minutes=self.reminder_lead_minutes)
        return await self._repo.due_reminders(moment, lead)

    async def mark_reminded(self, event_id: int, *, briefing: bool = False, start: bool = False) -> None:
        await self._repo.mark_reminded(event_id, briefing=briefing, start=start)

    def pending_reminders(self, event: EventRow, now: datetime | None = None) -> list[tuple[str, str, datetime]]:
        """Какие напоминания пора отправить: [(вид, подпись, момент)]."""
        moment = now or datetime.now(UTC)
        pending: list[tuple[str, str, datetime]] = []
        if not event["active"]:
            return pending
        horizon = moment + timedelta(minutes=self.reminder_lead_minutes)
        if not event["reminded_briefing"]:
            briefing = datetime.fromisoformat(event["briefing_at"])
            if moment <= briefing <= horizon:
                pending.append(("briefing", "Сбор / Briefing", briefing))
        if not event["reminded_start"]:
            start = datetime.fromisoformat(event["start_at"])
            if moment <= start <= horizon:
                pending.append(("start", "Начало / Start", start))
        return pending

    # --- представления ---

    async def embed(self, event: EventRow) -> discord.Embed:
        counts = await self._repo.signup_counts(int(event["id"]))
        label, emoji, color = EVENT_TYPES.get(str(event["event_type"]), EVENT_TYPES["freeform"])
        embed = discord.Embed(
            title=truncate(str(event["name"]), MAX_NAME),
            color=discord.Color.red() if not event["active"] else color,
        )
        if event["description"]:
            embed.add_field(name="Описание", value=truncate(str(event["description"]), MAX_DESCRIPTION))
        embed.add_field(
            name="Сбор / Briefing",
            value=f"<t:{_ts(event['briefing_at'])}:f> · <t:{_ts(event['briefing_at'])}:R>",
            inline=True,
        )
        embed.add_field(
            name="Начало / Start",
            value=f"<t:{_ts(event['start_at'])}:f> · <t:{_ts(event['start_at'])}:R>",
            inline=True,
        )
        embed.add_field(name="ТИП", value=f"{emoji} {label}", inline=True)
        for role in SIGNUP_ROLES:
            users = counts.get(role, [])
            if role == "not_going" and not event["show_not_going"] and not users:
                continue
            sign_label, sign_emoji = EVENT_SIGNUP_OPTIONS[role]
            value = "\n".join(f"<@{uid}>" for uid in users) or "—"
            embed.add_field(name=f"{sign_emoji} {sign_label} ({len(users)})", value=truncate(value, 1000), inline=True)
        embed.add_field(name="Создал", value=f"<@{event['creator_id']}>", inline=False)
        if event["image_url"]:
            embed.set_image(url=str(event["image_url"]))
        embed.set_footer(text=f"{embeds.BOT_NAME}  •  ивент #{event['id']}")
        if not event["active"]:
            embed.title = f"{embed.title} — отменён"
        return embed

    async def preview_embed(self, data: dict[str, Any], show_not_going: bool) -> discord.Embed:
        """Превью перед публикацией: записи ещё нет, списки пустые."""
        embed = discord.Embed(title=truncate(clean_name(str(data.get("name", ""))), MAX_NAME), color=embeds.INFO)
        description = clean_description(str(data.get("description", "")))
        if description:
            embed.add_field(name="Описание", value=truncate(description, MAX_DESCRIPTION))
        for key, name in (("briefing_at", "Сбор / Briefing"), ("start_at", "Начало / Start")):
            value = data.get(key)
            if isinstance(value, datetime):
                embed.add_field(
                    name=name,
                    value=f"<t:{int(value.timestamp())}:f> · <t:{int(value.timestamp())}:R>",
                    inline=True,
                )
        for role in SIGNUP_ROLES:
            sign_label, sign_emoji = EVENT_SIGNUP_OPTIONS[role]
            if role == "not_going" and not show_not_going:
                continue
            embed.add_field(name=f"{sign_emoji} {sign_label} (0)", value="—", inline=True)
        image_url = str(data.get("image_url") or "")
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text=f"{embeds.BOT_NAME}  •  превью")
        return embed


def _ts(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())
