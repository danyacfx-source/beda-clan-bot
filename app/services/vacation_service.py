"""Система отпусков: правила заявок, продления и отмены, сборка информационного сообщения.

Чистые функции (разбор дат, проверка периода, текст информационного сообщения)
не ходят в БД и в Discord — их можно проверять напрямую. Сервис добавляет
хранение, обновление сообщений в канале и запись действий в журнал.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import discord

from app.core.base import BaseService
from app.db.vacations_repository import STATUS_ACTIVE, STATUS_CANCELLED

if TYPE_CHECKING:
    from app.core.bot import ClanBot
    from app.db.vacations_repository import VacationsRepository
    from app.services.logging_service import LoggingService
    from app.types import VacationRow, VacationSettingsRow

logger = logging.getLogger("bot.services")

DATE_FORMAT = "%d.%m.%Y"
MAX_REASON_LENGTH = 300
INFO_DESCRIPTION_LIMIT = 4096
#: Запас под хвост «и ещё N», который добавляется при обрезке списка.
_TRUNCATION_RESERVE = 40

INFO_TITLE = "Система отпусков | Информация"
INFO_COLOR = discord.Color(0xF0932B)
INFO_EMPTY_TEXT = "Сейчас никто не в отпуске."

PANEL_TITLE = "Система отпусков | Панель"
PANEL_DESCRIPTION = (
    "Нажмите кнопку ниже, чтобы подать заявку на отпуск.\n\n"
    "🔷 **Взять отпуск** — подать заявку\n"
    "🔷 **Статус** — посмотреть свои отпуска\n"
    "🔷 **Продлить отпуск** — продлить текущий отпуск\n"
    "🔷 **Снять отпуск** — отменить текущий отпуск"
)

_DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")


class VacationError(ValueError):
    """Нарушение правил отпуска, которое показывается пользователю."""


def moscow_today() -> date:
    """Сегодняшняя дата по Москве (как у ивентов: без зависимости от tzdata)."""
    return (datetime.now(UTC) + timedelta(hours=3)).date()


def parse_date(text: str) -> date:
    """Разбирает дату ``ДД.ММ.ГГГГ``; несуществующие дни отклоняются."""
    match = _DATE_RE.match(text or "")
    if match is None:
        raise VacationError("Дата указывается в формате `ДД.ММ.ГГГГ`, например `05.08.2026`.")
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        raise VacationError(f"Такой даты не существует: `{text.strip()}`.") from None


def format_date(value: date | str) -> str:
    """Дата в виде ``ДД.ММ.ГГГГ``; принимает и ``date``, и ISO-строку из БД."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return value.strftime(DATE_FORMAT)


def format_period(start: date | str, end: date | str) -> str:
    return f"от {format_date(start)} до {format_date(end)}"


def validate_period(start: date, end: date, today: date) -> None:
    """Проверяет, что начало не в прошлом и окончание не раньше начала. Длина не ограничена."""
    if start < today:
        raise VacationError("Дата начала не может быть в прошлом. Укажите сегодняшний или будущий день.")
    if end < start:
        raise VacationError("Дата окончания не может быть раньше даты начала.")


def validate_reason(text: str) -> str:
    reason = (text or "").strip()
    if not reason:
        raise VacationError("Укажите причину отпуска.")
    if len(reason) > MAX_REASON_LENGTH:
        raise VacationError(f"Причина не длиннее {MAX_REASON_LENGTH} символов (сейчас {len(reason)}).")
    return reason


def live_rows(rows: list[VacationRow], today: date) -> list[VacationRow]:
    """Текущие и будущие отпуска, по дате начала."""
    live = [row for row in rows if date.fromisoformat(row["end_date"]) >= today]
    return sorted(live, key=lambda row: (row["start_date"], row["id"]))


def pick_target(rows: list[VacationRow], today: date) -> VacationRow | None:
    """Отпуск, который продлевается или снимается: текущий, иначе ближайший будущий."""
    live = live_rows(rows, today)
    for row in live:
        if date.fromisoformat(row["start_date"]) <= today:
            return row
    return live[0] if live else None


def build_info_text(rows: list[VacationRow], today: date) -> str:
    """Текст информационного сообщения; при переполнении лимита эмбеда хвост обрезается."""
    live = live_rows(rows, today)
    if not live:
        return INFO_EMPTY_TEXT
    blocks = [f"<@{row['user_id']}>\n{format_period(row['start_date'], row['end_date'])}\n{row['reason']}" for row in live]
    text = "\n\n".join(blocks)
    if len(text) <= INFO_DESCRIPTION_LIMIT:
        return text
    budget = INFO_DESCRIPTION_LIMIT - _TRUNCATION_RESERVE
    kept: list[str] = []
    used = 0
    for block in blocks:
        cost = len(block) + (2 if kept else 0)
        if used + cost > budget:
            break
        kept.append(block)
        used += cost
    hidden = len(blocks) - len(kept)
    result = "\n\n".join(kept)
    if hidden:
        result += f"\n\n… и ещё {hidden}"
    return result[:INFO_DESCRIPTION_LIMIT]


def info_embed(description: str) -> discord.Embed:
    return discord.Embed(title=INFO_TITLE, description=description, color=INFO_COLOR)


def panel_embed() -> discord.Embed:
    return discord.Embed(title=PANEL_TITLE, description=PANEL_DESCRIPTION, color=discord.Color.blurple())


def _one_vacation_message(current: VacationRow) -> str:
    return (
        f"У вас уже есть отпуск {format_period(current['start_date'], current['end_date'])}. "
        "Одновременно может быть только один отпуск. Чтобы изменить его срок, нажмите «Продлить отпуск»; "
        "чтобы отменить и оформить новый, нажмите «Снять отпуск»."
    )


class VacationService(BaseService["VacationsRepository"]):
    repo: VacationsRepository

    def __init__(
        self,
        repo: VacationsRepository,
        bot: ClanBot | None = None,
        log_service: LoggingService | None = None,
    ) -> None:
        super().__init__(repo)
        self._bot = bot
        self._log_service = log_service

    # --- заявки ---

    async def create(
        self,
        guild_id: int,
        user_id: int,
        start_text: str,
        end_text: str,
        reason: str,
        *,
        today: date | None = None,
    ) -> VacationRow:
        today = today or moscow_today()
        start = parse_date(start_text)
        end = parse_date(end_text)
        validate_period(start, end, today)
        text = validate_reason(reason)
        current = await self.list_for_user(guild_id, user_id, today=today)
        if current:
            raise VacationError(_one_vacation_message(current[0]))
        created_at = datetime.now(UTC).isoformat()
        vacation_id = await self._repo.insert(guild_id, user_id, start.isoformat(), end.isoformat(), text, created_at)
        row: VacationRow = {
            "id": vacation_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "reason": text,
            "created_at": created_at,
            "status": STATUS_ACTIVE,
            "updated_at": None,
        }
        await self._log(
            guild_id,
            "Отпуск оформлен",
            f"Участник: {user_id} | {format_period(row['start_date'], row['end_date'])} | Причина: {text}",
        )
        return row

    async def list_for_user(self, guild_id: int, user_id: int, *, today: date | None = None) -> list[VacationRow]:
        rows = await self._repo.for_user(guild_id, user_id)
        return live_rows(rows, today or moscow_today())

    async def target_for(self, guild_id: int, user_id: int, *, today: date | None = None) -> VacationRow | None:
        rows = await self._repo.for_user(guild_id, user_id)
        return pick_target(rows, today or moscow_today())

    async def extend(self, guild_id: int, user_id: int, end_text: str, *, today: date | None = None) -> VacationRow:
        target = await self.target_for(guild_id, user_id, today=today)
        if target is None:
            raise VacationError("Нет активного или предстоящего отпуска, который можно продлить.")
        new_end = parse_date(end_text)
        current_end = date.fromisoformat(target["end_date"])
        if new_end <= current_end:
            raise VacationError(f"Новая дата окончания должна быть позже текущей ({format_date(current_end)}).")
        updated_at = datetime.now(UTC).isoformat()
        await self._repo.update_end(target["id"], new_end.isoformat(), updated_at)
        result: VacationRow = {**target, "end_date": new_end.isoformat(), "updated_at": updated_at}
        await self._log(
            guild_id,
            "Отпуск продлён",
            f"Участник: {user_id} | {format_period(result['start_date'], result['end_date'])}",
        )
        return result

    async def cancel(self, guild_id: int, user_id: int, *, today: date | None = None) -> VacationRow:
        target = await self.target_for(guild_id, user_id, today=today)
        if target is None:
            raise VacationError("Нет активного или предстоящего отпуска для отмены.")
        updated_at = datetime.now(UTC).isoformat()
        await self._repo.set_status(target["id"], STATUS_CANCELLED, updated_at)
        result: VacationRow = {**target, "status": STATUS_CANCELLED, "updated_at": updated_at}
        await self._log(
            guild_id,
            "Отпуск снят",
            f"Участник: {user_id} | {format_period(result['start_date'], result['end_date'])}",
        )
        return result

    async def mark_ended(self, *, today: date | None = None) -> int:
        """Переводит в статус ``ended`` активные отпуска, у которых дата окончания прошла."""
        day = (today or moscow_today()).isoformat()
        due = await self._repo.active_ended_before(day)
        if not due:
            return 0
        count = await self._repo.mark_ended_before(day, datetime.now(UTC).isoformat())
        for row in due:
            await self._log(
                row["guild_id"],
                "Отпуск завершён автоматически",
                f"Участник: {row['user_id']} | {format_period(row['start_date'], row['end_date'])}",
            )
        return count

    # --- журнал ---

    async def _log(self, guild_id: int, title: str, description: str) -> None:
        """Пишет действие в журнал. Ошибки журнала не должны ломать заявку."""
        if self._log_service is None or self._bot is None:
            return
        try:
            guild = self._bot.get_guild(guild_id)
            if guild is None:
                return
            await self._log_service.log_event(guild, title, description)
        except Exception:
            logger.warning("Vacations: не удалось записать действие в журнал", exc_info=True)

    # --- информационное сообщение и панель ---

    async def info_description(self, guild_id: int, *, today: date | None = None) -> str:
        rows = await self._repo.for_guild(guild_id)
        return build_info_text(rows, today or moscow_today())

    async def settings(self, guild_id: int) -> VacationSettingsRow | None:
        return await self._repo.get_settings(guild_id)

    async def save_setup(self, guild_id: int, channel_id: int, info_message_id: int, panel_message_id: int) -> None:
        await self._repo.save_settings(guild_id, channel_id, info_message_id, panel_message_id)

    async def _text_channel(self, guild_id: int, settings: VacationSettingsRow) -> discord.TextChannel | None:
        """Текстовый канал настройки. Если канала нет или бот не может в него зайти — ссылки сбрасываются."""
        if self._bot is None:
            return None
        channel_id = int(settings["channel_id"])
        channel = self._bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                logger.warning("Vacations: канал %s недоступен — ссылки на сообщения сброшены", channel_id)
                await self._repo.clear_messages(guild_id)
                return None
            except discord.HTTPException:
                logger.warning("Vacations: не удалось получить канал %s — повторим позже", channel_id, exc_info=True)
                return None
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Vacations: канал %s не текстовый — ссылки на сообщения сброшены", channel_id)
            await self._repo.clear_messages(guild_id)
            return None
        return channel

    async def refresh_info(self, guild_id: int) -> None:
        """Перерисовывает информационное сообщение; если его удалили — публикует заново. Ошибки Discord не пробрасывает."""
        settings = await self._repo.get_settings(guild_id)
        if settings is None or settings["info_message_id"] is None:
            return
        channel = await self._text_channel(guild_id, settings)
        if channel is None:
            return
        message_id = int(settings["info_message_id"])
        description = await self.info_description(guild_id)
        try:
            await channel.get_partial_message(message_id).edit(
                embed=info_embed(description), allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.NotFound:
            logger.warning("Vacations: информационное сообщение #%s удалено — публикуем заново", message_id)
            await self._repost_info(guild_id, channel, description)
        except discord.Forbidden:
            logger.warning("Vacations: нет прав на информационное сообщение #%s — ссылки сброшены", message_id)
            await self._repo.clear_messages(guild_id)
        except discord.HTTPException:
            logger.warning("Vacations: не удалось обновить информационное сообщение #%s", message_id, exc_info=True)

    async def _repost_info(self, guild_id: int, channel: discord.TextChannel, description: str) -> None:
        try:
            message = await channel.send(embed=info_embed(description), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Vacations: не удалось переопубликовать информацию в канале %s — ссылки сброшены", channel.id, exc_info=True)
            await self._repo.clear_messages(guild_id)
            return
        await self._repo.set_info_message(guild_id, message.id)

    async def check_panel(self, guild_id: int, make_view: Callable[[], discord.ui.View]) -> None:
        """Проверяет панель; если её удалили — публикует заново с новым id. Ошибки Discord не пробрасывает."""
        settings = await self._repo.get_settings(guild_id)
        if settings is None or settings["panel_message_id"] is None:
            return
        channel = await self._text_channel(guild_id, settings)
        if channel is None:
            return
        message_id = int(settings["panel_message_id"])
        try:
            await channel.get_partial_message(message_id).fetch()
        except discord.NotFound:
            logger.warning("Vacations: панель #%s удалена — публикуем заново", message_id)
            await self._repost_panel(guild_id, channel, make_view)
        except discord.Forbidden:
            logger.warning("Vacations: нет доступа к панели #%s — ссылки сброшены", message_id)
            await self._repo.clear_messages(guild_id)
        except discord.HTTPException:
            logger.warning("Vacations: не удалось проверить панель #%s", message_id, exc_info=True)

    async def _repost_panel(self, guild_id: int, channel: discord.TextChannel, make_view: Callable[[], discord.ui.View]) -> None:
        try:
            message = await channel.send(embed=panel_embed(), view=make_view())
        except discord.HTTPException:
            logger.warning("Vacations: не удалось переопубликовать панель в канале %s — ссылки сброшены", channel.id, exc_info=True)
            await self._repo.clear_messages(guild_id)
            return
        await self._repo.set_panel_message(guild_id, message.id)

    async def refresh_all(self, make_panel_view: Callable[[], discord.ui.View] | None = None) -> None:
        """Обслуживание всех гильдий: информация и (если передана фабрика) панель. Сбой одной не мешает другим."""
        for settings in await self._repo.all_settings():
            guild_id = int(settings["guild_id"])
            try:
                await self.refresh_info(guild_id)
                if make_panel_view is not None:
                    await self.check_panel(guild_id, make_panel_view)
            except Exception:
                logger.exception("Vacations: ошибка обслуживания гильдии %s", guild_id)
