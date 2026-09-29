"""Ивенты (сборы): мастер создания в ЛС, отметки участия, напоминания.

Перенос VacationBot ``cogs/events.py``. Отличия от оригинала: состояние мастера
в сервисе, время в ISO UTC, проверка порядка «сбор ≤ начало», ID ивента из БД
(а не парсингом footer эмбеда) и флаги напоминаний в БД.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.core import embeds
from app.core.base import ClanCog
from app.core.views import EVENT_SIGNUP_OPTIONS, EventSignupView
from app.services.event_service import (
    FIELD_LABELS,
    EventFlow,
    EventValidationError,
    clean_description,
    clean_image_url,
    clean_name,
    format_event_datetime,
    parse_event_datetime,
)
from app.utils.format import truncate

if TYPE_CHECKING:
    from app.core.bot import ClanBot
    from app.services.event_service import EventService
    from app.types import EventRow

logger = logging.getLogger("bot.cogs")

_STEP_NAME = "name"
_STEP_TYPE = "type"
_STEP_DESCRIPTION = "description"
_STEP_BRIEFING = "briefing"
_STEP_START = "start"
_STEP_IMAGE = "image"
_STEP_NOT_GOING = "not_going"
_STEP_PUBLISH = "publish"
_STEP_EDIT_FIELD = "edit_field"
_STEP_EDIT_VALUE = "edit_value"

_EDITABLE = ("name", "description", "briefing_at", "start_at", "image_url")
_TYPE_CHOICES = (("competitive", "⚔️", "Компетитив"), ("freeform", "🧩", "Свободная форма"))
_DATE_HINT = "Формат: `ДД.ММ.ГГГГ ЧЧ:ММ` по Москве.\nПример: `25.04.2026 19:00`"
_CANCEL_WORDS = ("отмена", "cancel", "стоп", "хватит")

ViewCallback = Callable[[discord.Interaction], Awaitable[Any]]


def _bind(item: discord.ui.Item, callback: ViewCallback) -> None:
    """Вешает обработчик на item, созданный вне декоратора (custom_id с токеном)."""
    item.callback = callback  # type: ignore[method-assign]


class _FlowView(discord.ui.View):
    """Базовый View мастера: доступен только инициатору, с жёстким таймаутом."""

    def __init__(self, user_id: int, token: int, service: EventService, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.token = token
        self.service = service

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            embed=embeds.error("Не для тебя", "Этот выбор открыт для другого участника."), ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        self.service.flows.discard_token(self.token)

    async def _require_flow(self, interaction: discord.Interaction) -> EventFlow | None:
        flow = self.service.flows.get_by_token(self.user_id, self.token)
        if flow is not None:
            return flow
        self.stop()
        await interaction.response.edit_message(
            embed=embeds.error("Состояние утеряно", "Мастер истёк. Начните заново командой /event."), view=None
        )
        return None


class _ChoiceView(_FlowView):
    """Две кнопки, записывающие строковое значение в поток мастера."""

    def __init__(
        self,
        user_id: int,
        token: int,
        service: EventService,
        *,
        title: str,
        choices: tuple[tuple[str, str, discord.ButtonStyle], ...],
        on_pick: Callable[[EventService, EventFlow, str], Awaitable[None]],
    ) -> None:
        super().__init__(user_id, token, service)
        self._on_pick = on_pick
        for value, label, style in choices:
            button = discord.ui.Button(label=label, style=style, custom_id=f"event_pick:{value}")
            _bind(button, self._make(value))
            self.add_item(button)
        self._title = title

    def _make(self, value: str) -> ViewCallback:
        async def callback(interaction: discord.Interaction) -> None:
            flow = await self._require_flow(interaction)
            if flow is None:
                return
            self.stop()
            flow.touch()
            await self._on_pick(self.service, flow, value)

        return callback

    async def on_timeout(self) -> None:
        self.service.flows.discard_token(self.token)


class EventTypeView(_ChoiceView):
    def __init__(self, user_id: int, token: int, service: EventService) -> None:
        super().__init__(
            user_id,
            token,
            service,
            title="тип",
            choices=(
                ("competitive", "⚔️ Компетитив", discord.ButtonStyle.primary),
                ("freeform", "🧩 Свободная форма", discord.ButtonStyle.secondary),
            ),
            on_pick=self._store_type,
        )

    async def _store_type(self, service: EventService, flow: EventFlow, value: str) -> None:
        del service
        flow.data["event_type"] = value
        flow.step = _STEP_DESCRIPTION


class NotGoingView(_ChoiceView):
    def __init__(self, user_id: int, token: int, service: EventService) -> None:
        super().__init__(
            user_id,
            token,
            service,
            title="не иду",
            choices=(
                ("yes", "Показывать «Не иду»", discord.ButtonStyle.success),
                ("no", "Скрыть «Не иду»", discord.ButtonStyle.secondary),
            ),
            on_pick=self._store_flag,
        )

    async def _store_flag(self, service: EventService, flow: EventFlow, value: str) -> None:
        del service
        flow.data["show_not_going"] = value == "yes"
        flow.step = _STEP_PUBLISH


class PublishView(_FlowView):
    def __init__(self, user_id: int, token: int, service: EventService) -> None:
        super().__init__(user_id, token, service)
        for value, label, style in (
            ("yes", "Опубликовать", discord.ButtonStyle.success),
            ("no", "Отменить", discord.ButtonStyle.danger),
        ):
            button = discord.ui.Button(label=label, emoji="📣" if value == "yes" else "✖️", style=style)
            _bind(button, self._make(value))
            self.add_item(button)

    def _make(self, publish: str) -> ViewCallback:
        async def callback(interaction: discord.Interaction) -> None:
            flow = await self._require_flow(interaction)
            if flow is None:
                return
            self.stop()
            if publish != "yes":
                self.service.flows.discard(self.user_id)
                await interaction.response.edit_message(embed=embeds.info("Создание отменено", "Ивент не опубликован."), view=None)
                return
            await _publish(self.service, interaction, flow)

        return callback


class EditFieldView(_FlowView):
    def __init__(self, user_id: int, token: int, service: EventService) -> None:
        super().__init__(user_id, token, service)
        select = discord.ui.Select(
            placeholder="Что изменить?",
            options=[discord.SelectOption(label=FIELD_LABELS[name], value=name) for name in _EDITABLE],
        )
        _bind(select, self._on_select)
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        flow = await self._require_flow(interaction)
        if flow is None:
            return
        select = self.children[0]
        assert isinstance(select, discord.ui.Select)
        field = select.values[0]
        flow.edit_field = field
        flow.step = _STEP_EDIT_VALUE
        flow.touch()
        event = await self.service.get(flow.event_id) if flow.event_id else None
        current = truncate(str((event or {}).get(field, "")), 120) or "(пусто)"
        hint = (
            "Прикрепите новое изображение или отправьте `-`, чтобы убрать его."
            if field == "image_url"
            else f"Введите новое значение. {_DATE_HINT}"
        )
        await interaction.response.edit_message(
            embed=embeds.info(f"Поле: {FIELD_LABELS[field]}", f"Сейчас: **{current}**\n\n{hint}"),
            view=None,
        )


class EditConfirmView(_FlowView):
    def __init__(self, user_id: int, token: int, service: EventService) -> None:
        super().__init__(user_id, token, service)
        for value, label, style in (
            ("yes", "Сохранить", discord.ButtonStyle.success),
            ("no", "Отмена", discord.ButtonStyle.danger),
        ):
            button = discord.ui.Button(label=label, style=style)
            _bind(button, self._make(value))
            self.add_item(button)

    def _make(self, save: str) -> ViewCallback:
        async def callback(interaction: discord.Interaction) -> None:
            flow = await self._require_flow(interaction)
            if flow is None:
                return
            self.stop()
            self.service.flows.discard(self.user_id)
            if save != "yes":
                await interaction.response.edit_message(embed=embeds.info("Правка отменена", "Ивент не изменён."), view=None)
                return
            await _apply_edit(self.service, interaction, flow)

        return callback


async def _publish(service: EventService, interaction: discord.Interaction, flow: EventFlow) -> None:
    channel = interaction.client.get_channel(flow.channel_id)
    if not isinstance(channel, discord.TextChannel):
        service.flows.discard(flow.user_id)
        await interaction.response.edit_message(
            embed=embeds.error("Канал недоступен", "Канал, из которого запущен мастер, больше не найден."), view=None
        )
        return
    try:
        event_id = await service.create(
            guild_id=flow.guild_id,
            channel_id=flow.channel_id,
            creator_id=flow.user_id,
            name=str(flow.data.get("name", "")),
            event_type=str(flow.data.get("event_type", "freeform")),
            description=str(flow.data.get("description", "")),
            briefing_at=flow.data["briefing_at"],
            start_at=flow.data["start_at"],
            image_url=str(flow.data.get("image_url", "")),
            show_not_going=bool(flow.data.get("show_not_going", False)),
        )
    except (EventValidationError, KeyError) as exc:
        service.flows.discard(flow.user_id)
        await interaction.response.edit_message(
            embed=embeds.error("Не удалось создать", str(exc) or "Мастер заполнен не полностью."), view=None
        )
        return
    service.flows.discard(flow.user_id)

    event = await service.get(event_id)
    if event is None:
        await interaction.response.edit_message(embed=embeds.error("Ошибка", "Ивент создан, но не найден."), view=None)
        return
    view = EventSignupView(event_id)
    try:
        message = await channel.send(embed=await service.embed(event), view=view)
    except discord.HTTPException as exc:
        await service.cancel(event_id)
        await interaction.response.edit_message(embed=embeds.error("Не удалось опубликовать", f"Сообщение не отправлено: {exc}"), view=None)
        return
    await service.bind_message(event_id, message.id)
    interaction.client.add_view(view, message_id=message.id)
    await interaction.response.edit_message(embed=embeds.success("Ивент опубликован", f"Сообщение: {message.jump_url}"), view=None)


async def _apply_edit(service: EventService, interaction: discord.Interaction, flow: EventFlow) -> None:
    if flow.event_id is None or flow.edit_field is None:
        await interaction.response.edit_message(embed=embeds.error("Ошибка", "Нечего сохранять."), view=None)
        return
    event = await service.get(flow.event_id)
    if event is None:
        await interaction.response.edit_message(embed=embeds.error("Ивент не найден", "Возможно, он удалён."), view=None)
        return
    if not await service.is_creator(event, flow.user_id):
        await interaction.response.edit_message(
            embed=embeds.error("Недостаточно прав", "Редактировать может только создатель ивента."), view=None
        )
        return
    try:
        await service.update(event, flow.edit_field, str(flow.data.get("new_value", "")))
    except EventValidationError as exc:
        await interaction.response.edit_message(embed=embeds.error("Не сохранено", str(exc)), view=None)
        return
    updated = await service.get(flow.event_id)
    if updated is not None:
        await _redraw(interaction.client, updated)
    await interaction.response.edit_message(embed=embeds.success("Ивент обновлён", "Карточка в канале перерисована."), view=None)


async def _redraw(client: discord.Client, event: EventRow, *, cancelled: bool = False) -> None:
    """Перерисовывает сообщение ивента; удалённые сообщения игнорирует."""
    services = getattr(client, "services", None)
    channel = client.get_channel(int(event["channel_id"]))
    if services is None or not event["message_id"] or not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(int(event["message_id"]))
        embed = await services.events.embed(event)
        if cancelled:
            view = EventSignupView(int(event["id"]))
            for item in view.children:
                item.disabled = True
            await message.edit(embed=embed, view=view)
        else:
            await message.edit(embed=embed)
    except discord.HTTPException:
        logger.debug("Не удалось перерисовать сообщение ивента #%s", event["id"], exc_info=True)


class EventsCog(ClanCog, name="Events"):
    def __init__(self, bot: ClanBot, events: EventService) -> None:
        super().__init__(bot)
        self.events = events

    async def cog_load(self) -> None:
        self.reminder_loop.change_interval(seconds=self.config.events_check_interval_seconds)
        self.reminder_loop.start()

    async def cog_unload(self) -> None:
        self.reminder_loop.cancel()

    @tasks.loop(seconds=60.0)
    async def reminder_loop(self) -> None:
        if self.bot.is_closed():
            return
        try:
            due = await self.events.due_reminders()
        except Exception:
            logger.exception("Ошибка при выборке ивентов для напоминаний")
            return
        for event in due:
            try:
                await self._notify(event)
            except Exception:
                logger.exception("Ошибка напоминания по ивенту #%s", event["id"])

    @reminder_loop.before_loop
    async def before_reminder(self) -> None:
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            self.reminder_loop.cancel()

    async def _notify(self, event: EventRow) -> None:
        channel = self.bot.get_channel(int(event["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return
        for kind, label, moment in self.events.pending_reminders(event):
            participants = await self.events.participants(int(event["id"]))
            text = f"🔔 **{truncate(str(event['name']), 100)}** — {label} <t:{int(moment.timestamp())}:R>"
            content = (" ".join(f"<@{uid}>" for uid in participants) + f" {text}") if participants else text
            await channel.send(
                content=content,
                allowed_mentions=discord.AllowedMentions(users=participants[:100]),
            )
            await self.events.mark_reminded(int(event["id"]), briefing=kind == "briefing", start=kind == "start")

    # --- мастер ---

    @app_commands.command(name="event", description="Создать ивент: мастер задаёт вопросы в личных сообщениях")
    @app_commands.guild_only()
    async def event_cmd(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild_id is None:
            await interaction.response.send_message(embed=embeds.error("Только на сервере", "Команда работает на сервере."), ephemeral=True)
            return
        recent = await self.events.recent_for_guild(interaction.guild_id, limit=100)
        if sum(1 for row in recent if row["active"]) >= self.config.events_max_active_per_guild:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Слишком много ивентов",
                    f"На сервере уже {self.config.events_max_active_per_guild} активных ивентов. "
                    "Отмените прошедшие командой /event_cancel.",
                ),
                ephemeral=True,
            )
            return
        existing = self.events.flows.get(interaction.user.id)
        if existing is not None:
            await interaction.response.send_message(
                embed=embeds.warning(
                    "Мастер уже идёт",
                    "Закончите или отмените его в личных сообщениях (напишите `отмена`).",
                ),
                ephemeral=True,
            )
            return
        try:
            await interaction.user.send(embed=embeds.info("Шаг 1/7 — название", "Отправьте название ивента. Для отмены напишите `отмена`."))
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error(
                    "Закрыты личные сообщения",
                    "Откройте ЛС для бота, чтобы пройти мастер создания ивента.",
                ),
                ephemeral=True,
            )
            return

        self.events.flows.begin(
            EventFlow(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,  # type: ignore[arg-type]
                step=_STEP_NAME,
            )
        )
        await interaction.response.send_message(embed=embeds.success("Мастер начат", "Ответьте боту в личных сообщениях."), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not isinstance(message.channel, discord.DMChannel):
            return
        flow = self.events.flows.get(message.author.id)
        if flow is None:
            return
        text = message.content.strip()
        if text.lower() in _CANCEL_WORDS:
            self.events.flows.discard(message.author.id)
            await message.channel.send(embed=embeds.info("Отменено", "Мастер прерван."))
            return
        if flow.step == _STEP_EDIT_VALUE:
            await self._handle_edit_value(message, flow, text)
            return
        await self._handle_step(message, flow, text)

    async def _handle_step(self, message: discord.Message, flow: EventFlow, text: str) -> None:
        reply = message.channel.send
        if flow.step == _STEP_NAME:
            try:
                flow.data["name"] = clean_name(text)
            except EventValidationError as exc:
                await reply(embed=embeds.error("Не подходит", str(exc)))
                return
            flow.step = _STEP_TYPE
            flow.touch()
            await reply(
                embed=embeds.info("Шаг 2/7 — тип", "Выберите тип ивента."),
                view=EventTypeView(flow.user_id, self.events.flows.token_for(flow), self.events),
            )
        elif flow.step == _STEP_DESCRIPTION:
            flow.data["description"] = clean_description(text)
            flow.step = _STEP_BRIEFING
            flow.touch()
            await reply(embed=embeds.info("Шаг 4/7 — время сбора", _DATE_HINT))
        elif flow.step in (_STEP_BRIEFING, _STEP_START):
            try:
                moment = parse_event_datetime(text)
            except EventValidationError as exc:
                await reply(embed=embeds.error("Неверная дата", str(exc)))
                return
            if flow.step == _STEP_BRIEFING:
                flow.data["briefing_at"] = moment
                flow.step = _STEP_START
                flow.touch()
                await reply(
                    embed=embeds.info(
                        "Шаг 5/7 — время начала",
                        f"Сбор: **{format_event_datetime(moment)}** (МСК)\n\n{_DATE_HINT.replace('19:00', '20:00')}",
                    )
                )
            else:
                flow.data["start_at"] = moment
                flow.step = _STEP_IMAGE
                flow.touch()
                await reply(embed=embeds.info("Шаг 6/7 — изображение", "Прикрепите картинку или отправьте `-`."))
        elif flow.step == _STEP_IMAGE:
            if text == "-":
                flow.data["image_url"] = ""
            elif message.attachments:
                try:
                    flow.data["image_url"] = clean_image_url(message.attachments[0].url)
                except EventValidationError as exc:
                    await reply(embed=embeds.error("Не подходит", str(exc)))
                    return
            else:
                await reply(embed=embeds.error("Нужно изображение", "Прикрепите картинку или отправьте `-`."))
                return
            flow.step = _STEP_NOT_GOING
            flow.touch()
            await reply(
                embed=embeds.info("Шаг 7/7 — кнопка «Не иду»", "Показывать ли вариант отказа в карточке?"),
                view=NotGoingView(flow.user_id, self.events.flows.token_for(flow), self.events),
            )

    async def _handle_edit_value(self, message: discord.Message, flow: EventFlow, text: str) -> None:
        field = flow.edit_field
        if field is None:
            self.events.flows.discard(flow.user.id)
            return
        if field == "image_url":
            if text == "-":
                flow.data["new_value"] = ""
            elif message.attachments:
                try:
                    flow.data["new_value"] = clean_image_url(message.attachments[0].url)
                except EventValidationError as exc:
                    await message.channel.send(embed=embeds.error("Не подходит", str(exc)))
                    return
            else:
                await message.channel.send(embed=embeds.error("Нужно изображение", "Прикрепите картинку или отправьте `-`."))
                return
        else:
            flow.data["new_value"] = text
        flow.touch()
        value = truncate(str(flow.data["new_value"]), 120) or "(пусто)"
        await message.channel.send(
            embed=embeds.info("Подтвердите правку", f"Поле **{FIELD_LABELS[field]}** → `{value}`"),
            view=EditConfirmView(flow.user_id, self.events.flows.token_for(flow), self.events),
        )

    # --- управление ---

    async def _begin_edit(self, interaction: discord.Interaction, event: EventRow) -> None:
        if self.events.flows.get(interaction.user.id) is not None:
            await interaction.response.send_message(
                embed=embeds.warning("Мастер уже идёт", "Закончите или отмените его в личных сообщениях (`отмена`)."),
                ephemeral=True,
            )
            return
        try:
            await interaction.user.send(
                embed=embeds.info(
                    "Редактирование ивента",
                    f"Ивент: **{truncate(str(event['name']), 100)}**\nВыберите поле для правки.",
                )
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error("Закрыты личные сообщения", "Откройте ЛС, чтобы редактировать ивент."),
                ephemeral=True,
            )
            return
        flow = self.events.flows.begin(
            EventFlow(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id or 0,
                channel_id=interaction.channel_id or 0,
                step=_STEP_EDIT_FIELD,
                event_id=int(event["id"]),
            )
        )
        await interaction.user.send(view=EditFieldView(interaction.user.id, self.events.flows.token_for(flow), self.events))
        await interaction.response.send_message(embed=embeds.success("Проверьте ЛС", "Продолжим в личных сообщениях."), ephemeral=True)

    async def _lookup(self, interaction: discord.Interaction, message_id: int) -> EventRow | None:
        event = await self.events.get_by_message(message_id)
        if event is None or interaction.guild_id is None or int(event["guild_id"]) != interaction.guild_id:
            await interaction.response.send_message(
                embed=embeds.error("Ивент не найден", "Проверьте ID сообщения с ивентом."), ephemeral=True
            )
            return None
        return event

    @app_commands.command(name="event_edit", description="Изменить поля ивента, где вы создатель")
    @app_commands.describe(message_id="ID сообщения с ивентом")
    @app_commands.guild_only()
    async def event_edit(self, interaction: discord.Interaction, message_id: int) -> None:
        event = await self._lookup(interaction, message_id)
        if event is None:
            return
        if not await self.events.is_creator(event, interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.error("Недостаточно прав", "Редактировать может только создатель ивента."), ephemeral=True
            )
            return
        await self._begin_edit(interaction, event)

    @app_commands.command(name="event_cancel", description="Отменить ивент, где вы создатель")
    @app_commands.describe(message_id="ID сообщения с ивентом")
    @app_commands.guild_only()
    async def event_cancel(self, interaction: discord.Interaction, message_id: int) -> None:
        event = await self._lookup(interaction, message_id)
        if event is None:
            return
        if not await self.events.is_creator(event, interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.error("Недостаточно прав", "Отменить может только создатель ивента."), ephemeral=True
            )
            return
        if not event["active"]:
            await interaction.response.send_message(embed=embeds.info("Уже отменён", "Этот ивент уже отменён."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.events.cancel(int(event["id"]))
        updated = await self.events.get(int(event["id"]))
        if updated is not None:
            await _redraw(self.bot, updated, cancelled=True)
        await interaction.followup.send(embed=embeds.success("Ивент отменён", "Карточка в канале помечена отменой."), ephemeral=True)

    @app_commands.command(name="event_list", description="Показать последние ивенты сервера")
    @app_commands.guild_only()
    async def event_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.events.recent_for_guild(interaction.guild_id, limit=10)
        if not rows:
            await interaction.followup.send(embed=embeds.info("Ивентов нет", "На сервере пока не создано ивентов."), ephemeral=True)
            return
        embed = embeds.brand("Последние ивенты", f"Показано: {len(rows)}")
        for row in rows:
            status = "активен" if row["active"] else "отменён"
            embed.add_field(
                name=f"#{row['id']} · {truncate(str(row['name']), 60)}",
                value=f"{format_event_datetime(datetime.fromisoformat(row['start_at']))} (МСК) · {status}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="event_signup", description="Отметить участие в ивентах без кнопки")
    @app_commands.describe(event_id="ID ивента из подписи под карточкой", role="Ваш статус")
    @app_commands.choices(role=[app_commands.Choice(name=label, value=value) for value, (label, _) in EVENT_SIGNUP_OPTIONS.items()])
    @app_commands.guild_only()
    async def event_signup(self, interaction: discord.Interaction, event_id: int, role: str) -> None:
        event = await self.events.get(event_id)
        if event is None or interaction.guild_id is None or int(event["guild_id"]) != interaction.guild_id:
            await interaction.response.send_message(embed=embeds.error("Ивент не найден", "Проверьте ID ивента."), ephemeral=True)
            return
        try:
            added = await self.events.set_signup(event, interaction.user.id, role)
        except EventValidationError as exc:
            await interaction.response.send_message(embed=embeds.error("Не удалось", str(exc)), ephemeral=True)
            return
        label = EVENT_SIGNUP_OPTIONS[role][0]
        await interaction.response.send_message(
            embed=embeds.success("Готово", f"Статус **{label}** сохранён.")
            if added
            else embeds.info("Готово", f"Статус **{label}** снят."),
            ephemeral=True,
        )
        updated = await self.events.get(event_id)
        if updated is not None:
            await _redraw(self.bot, updated)
