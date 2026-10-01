"""Кастомные интерактивные представления (views)."""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import discord

from app.core import embeds

if TYPE_CHECKING:
    from app.services import Services
    from app.services.ticket_service import TicketService

logger = logging.getLogger("bot.views")

TICKET_OPEN_ID = "ticket:open"
TICKET_CLOSE_ID = "ticket:close"
GIVEAWAY_ENTER_ID = "giveaway:enter"
EVENT_SIGNUP_ID = "event:signup"
WHERE_PLAY_CALLER_ID = "where_play:caller"

#: Варианты участия в ивентах: значение в БД → (подпись, эмодзи).
EVENT_SIGNUP_OPTIONS: dict[str, tuple[str, str]] = {
    "going": ("Иду", "✅"),
    "maybe": ("Возможно", "🤔"),
    "not_going": ("Не иду", "❌"),
}

#: Отметки, оставшиеся в базе от старой схемы с выпадающим списком.
#: Новые отметки сюда не пишутся, но старые карточки должны их показывать.
EVENT_LEGACY_OPTIONS: dict[str, tuple[str, str]] = {
    "going_inf": ("Иду (пех)", "🪖"),
    "going_tech": ("Иду (тех)", "🚜"),
    "sl": ("SL", "🟠"),
    "camera": ("Камера", "📷"),
}

#: Коллеры карточки «Где играем».
MAX_CALLERS = 5
CALLER_ACTIVITIES: tuple[str, ...] = ("Штурм", "Стройка", "ДРГ", "Разведка")


def _event_id_from_custom_id(custom_id: str) -> int | None:
    """Достаёт ID ивента из ``event:signup:<id>`` или ``event:signup:<id>:<роль>``."""
    parts = custom_id.split(":")
    if len(parts) not in (3, 4) or parts[0] != "event" or parts[1] != "signup":
        return None
    return int(parts[2]) if parts[2].isdigit() else None


class ConfirmView(discord.ui.View):
    """Кнопка-подтверждение для необратимых действий.

    Опционально привязывается к пользователю: чужие нажатия игнорируются.
    """

    def __init__(
        self,
        on_confirm: Callable[[discord.Interaction], Awaitable[Any]] | None = None,
        on_cancel: Callable[[discord.Interaction], Awaitable[Any]] | None = None,
        *,
        timeout: float = 60.0,
        user: discord.User | None = None,
        confirm_label: str = "Подтвердить",
        cancel_label: str = "Отмена",
        confirm_emoji: str | None = None,
        cancel_emoji: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.user = user
        self.yes_btn.label = confirm_label
        self.no_btn.label = cancel_label
        if confirm_emoji:
            self.yes_btn.emoji = confirm_emoji
        if cancel_emoji:
            self.no_btn.emoji = cancel_emoji

    def _allowed(self, interaction: discord.Interaction) -> bool:
        return self.user is None or interaction.user.id == self.user.id

    def _disable_all_items(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    async def _default_cancel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=embeds.info("Действие отменено"), view=None)

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.success)
    async def yes_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._allowed(interaction):
            await interaction.response.defer()
            return
        self._disable_all_items()
        await interaction.response.edit_message(view=self)
        callback = self.on_confirm or self._default_cancel
        await callback(interaction)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no_btn(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self._allowed(interaction):
            await interaction.response.defer()
            return
        self._disable_all_items()
        await interaction.response.edit_message(view=self)
        callback = self.on_cancel or self._default_cancel
        await callback(interaction)

    async def on_timeout(self) -> None:
        self._disable_all_items()


class _TicketBaseView(discord.ui.View):
    ticket_service: TicketService

    def __init__(self, ticket_service: TicketService) -> None:
        super().__init__(timeout=None)
        self.ticket_service = ticket_service


class TicketOpenView(_TicketBaseView):
    """Устойчивая кнопка открытия тикета (работает после рестарта)."""

    def __init__(
        self,
        ticket_service: TicketService,
        *,
        label: str = "Открыть тикет",
        emoji: str | None = "🎫",
    ) -> None:
        super().__init__(ticket_service)
        self.open_ticket.label = label
        self.open_ticket.emoji = emoji or None

    @discord.ui.button(label="Открыть тикет", style=discord.ButtonStyle.success, custom_id=TICKET_OPEN_ID, emoji="🎫")
    async def open_ticket(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error("Не удалось открыть тикет", "Кнопка работает только на сервере."),
                ephemeral=True,
            )
            return
        result = await self.ticket_service.create(interaction.guild, interaction.user)
        if result.error:
            embed = embeds.error("Не удалось открыть тикет", result.error)
        else:
            channel = result.channel
            if channel is None:
                await interaction.response.send_message(
                    embed=embeds.error("Не удалось открыть тикет", "Канал тикета не был создан."),
                    ephemeral=True,
                )
                return
            embed = embeds.success("Тикет открыт", f"Перейдите в {channel.mention} и опишите вопрос одним сообщением.")
            embed.add_field(name="КАНАЛ ПОДДЕРЖКИ", value=channel.mention, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class GiveawayView(discord.ui.View):
    """Устойчивая кнопка участия в розыгрыше (работает после рестарта)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Участвовать", style=discord.ButtonStyle.success, custom_id=GIVEAWAY_ENTER_ID, emoji="🎉")
    async def enter(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        services: Services | None = getattr(interaction.client, "services", None)
        if services is None:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Сервисы недоступны."), ephemeral=True)
            return
        giveaway = await services.giveaways.get_by_message(interaction.message.id)
        if giveaway is None:
            await interaction.response.send_message(embed=embeds.error("Розыгрыш не найден"), ephemeral=True)
            return
        if interaction.guild_id is not None and giveaway["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=embeds.error("Не на этом сервере"), ephemeral=True)
            return
        if not giveaway["active"]:
            await interaction.response.send_message(embed=embeds.warning("Розыгрыш завершён"), ephemeral=True)
            return

        min_days = giveaway.get("min_days", 0)
        if min_days > 0 and interaction.guild is not None:
            member = interaction.guild.get_member(interaction.user.id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(interaction.user.id)
                except discord.HTTPException:
                    member = None
            if member is None or member.joined_at is None:
                await interaction.response.send_message(
                    embed=embeds.error("Ошибка", "Не удалось определить дату вашего вступления на сервер."), ephemeral=True
                )
                return
            joined = member.joined_at
            if joined.tzinfo is None:
                joined = joined.replace(tzinfo=UTC)
            delta = datetime.now(UTC) - joined
            if delta.days < min_days:
                await interaction.response.send_message(
                    embed=embeds.error(
                        "Не выполнено условие",
                        f"Для участия нужно находиться на сервере минимум {min_days} дн. (вы на сервере {delta.days} дн.).",
                    ),
                    ephemeral=True,
                )
                return

        added = await services.giveaways.join(giveaway["id"], interaction.user.id)
        embed = (
            embeds.success("Заявка принята", "Вы участвуете в розыгрыше. Результат появится здесь после завершения.")
            if added
            else embeds.info("Вы уже участвуете", "Повторно нажимать кнопку не нужно.")
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            embed = await services.giveaways.embed(giveaway)
            await interaction.message.edit(embed=embed)
        except discord.HTTPException:
            pass


class PollView(discord.ui.View):
    """Кнопки голосования в опросе. Переживает рестарт (данные в БД, view перерегистрируется)."""

    def __init__(self, poll_id: int, option_count: int) -> None:
        super().__init__(timeout=None)
        for index in range(option_count):
            self.add_item(_PollOptionButton(poll_id, index))


class _PollOptionButton(discord.ui.Button):
    def __init__(self, poll_id: int, index: int) -> None:
        super().__init__(label=str(index + 1), custom_id=f"poll:{poll_id}:{index}", style=discord.ButtonStyle.primary)
        self.poll_id = poll_id
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        services: Services | None = getattr(interaction.client, "services", None)
        if services is None:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Сервисы недоступны."), ephemeral=True)
            return
        poll = await services.polls.get(self.poll_id)
        if poll is None or not poll["active"]:
            await interaction.response.send_message(embed=embeds.warning("Опрос завершён"), ephemeral=True)
            return
        if interaction.guild_id is not None and poll["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=embeds.error("Не на этом сервере"), ephemeral=True)
            return
        option_count = len(json.loads(poll["options"]))
        if self.index >= option_count:
            await interaction.response.send_message(embed=embeds.error("Вариант недоступен"), ephemeral=True)
            return
        status = await services.polls.vote(self.poll_id, interaction.user.id, self.index, option_count)
        embed = await services.polls.embed(self.poll_id)
        await interaction.response.edit_message(embed=embed)
        if status == 2:
            notice = embeds.success("Голос учтён")
        elif status == 1:
            notice = embeds.info("Голос изменён")
        else:
            notice = embeds.info("Вы уже голосовали за этот вариант")
        await interaction.followup.send(embed=notice, ephemeral=True)


async def _handle_event_signup(interaction: discord.Interaction, role: str) -> None:
    """Общая обработка кнопок «Иду»/«Не иду» в карточке ивента."""
    services: Services | None = getattr(interaction.client, "services", None)
    if services is None:
        await interaction.response.send_message(embed=embeds.error("Ошибка", "Сервисы недоступны."), ephemeral=True)
        return
    event = None
    # interaction.data — это dict из JSON, а не объект: getattr по нему молча даёт "".
    custom_id = str((interaction.data or {}).get("custom_id") or "")
    event_id = _event_id_from_custom_id(custom_id)
    if event_id is None:
        await interaction.response.send_message(embed=embeds.error("Ошибка", "Ивент не распознан."), ephemeral=True)
        return
    event = await services.events.get(event_id)
    if event is None:
        await interaction.response.send_message(embed=embeds.error("Ивент не найден"), ephemeral=True)
        return
    if interaction.guild_id is not None and int(event["guild_id"]) != interaction.guild_id:
        await interaction.response.send_message(
            embed=embeds.error("Не на этом сервере", "Этот ивент создан на другом сервере."), ephemeral=True
        )
        return
    if not event["active"]:
        await interaction.response.send_message(embed=embeds.warning("Ивент отменён"), ephemeral=True)
        return
    try:
        added = await services.events.set_signup(event, interaction.user.id, role)
    except ValueError as exc:
        await interaction.response.send_message(embed=embeds.error("Не удалось", str(exc)), ephemeral=True)
        return
    label = EVENT_SIGNUP_OPTIONS[role][0]
    notice = (
        embeds.success("Отметка принята", f"Ваш статус: **{label}**.")
        if added
        else embeds.info("Отметка снята", f"Статус **{label}** снят.")
    )
    await interaction.response.send_message(embed=notice, ephemeral=True)
    # Счётчики в карточке должны отражать только что записанную отметку,
    # поэтому перечитываем ивент, а не переиспользуем прочитанный ранее.
    fresh = await services.events.get(int(event["id"]))
    if fresh is not None and interaction.message is not None:
        try:
            await interaction.message.edit(embed=await services.events.embed(fresh))
        except discord.HTTPException:
            pass


class EventSignupView(discord.ui.View):
    """Устойчивые кнопки участия в ивентах (работают после рестарта)."""

    def __init__(self, event_id: int) -> None:
        super().__init__(timeout=None)
        for value, (label, emoji) in EVENT_SIGNUP_OPTIONS.items():
            style = discord.ButtonStyle.danger if value == "not_going" else discord.ButtonStyle.success
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"{EVENT_SIGNUP_ID}:{event_id}:{value}",
                emoji=emoji,
            )
            button.callback = self._make(value)  # type: ignore[method-assign]
            self.add_item(button)

    def _make(self, role: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await _handle_event_signup(interaction, role)

        return callback


class WherePlayCallerView(discord.ui.View):
    """Кнопка «Я коллер» на карточке «Где играем» (переживает рестарт)."""

    def __init__(self, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.caller.disabled = disabled

    @discord.ui.button(
        label="Я коллер",
        emoji="🎖️",
        style=discord.ButtonStyle.success,
        custom_id=WHERE_PLAY_CALLER_ID,
    )
    async def caller(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        services: Services | None = getattr(interaction.client, "services", None)
        if services is None:
            await interaction.response.send_message(embed=embeds.error("Ошибка", "Сервисы недоступны."), ephemeral=True)
            return
        await _handle_caller_press(services, interaction)


async def _handle_caller_press(services: Services, interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(embed=embeds.error("Только на сервере", "Кнопка работает на сервере."), ephemeral=True)
        return True
    await interaction.response.defer(ephemeral=True, thinking=True)
    row = await services.where_play.row(interaction.guild_id)
    if row is None or not row["active"] or not row["message_id"]:
        await interaction.followup.send(embed=embeds.error("Сбор не активен", "Администратор должен запустить общий сбор."), ephemeral=True)
        return True
    if not services.where_play.is_manager(row, interaction.user):
        await interaction.followup.send(
            embed=embeds.error(
                "Недостаточно прав",
                "Кнопка доступна администраторам и роли коллеров из /setup_where_play.",
            ),
            ephemeral=True,
        )
        return True
    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.followup.send(
            embed=embeds.warning(
                "Сначала в голос",
                "Зайдите в любой голосовой канал, затем нажмите «Я коллер» — бот перенесёт вас в комнату.",
            ),
            ephemeral=True,
        )
        return True

    category = interaction.guild.get_channel(interaction.client.config.temp_voice_category_id or 0)
    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(
            embed=embeds.error(
                "Не настроено",
                "Администратор должен указать категорию голосовых комнат (TEMP_VOICE_CATEGORY_ID).",
            ),
            ephemeral=True,
        )
        return True
    permissions = category.permissions_for(interaction.user)
    if not permissions.view_channel or not permissions.connect:
        await interaction.followup.send(
            embed=embeds.error("Нет доступа", "У вас нет доступа к категории голосовых комнат."), ephemeral=True
        )
        return True
    if not category.permissions_for(interaction.guild.me).move_members:
        await interaction.followup.send(
            embed=embeds.error("Не хватает прав бота", "Боту нужно право «Перемещать участников» в этой категории."),
            ephemeral=True,
        )
        return True

    owners, count = await services.where_play.caller_slots(interaction.guild)
    if interaction.user.id not in owners and count >= MAX_CALLERS:
        await interaction.followup.send(
            embed=embeds.warning(
                "Лимит комнат",
                f"Уже есть {MAX_CALLERS} комнат коллеров. Новый коллер сможет присоединиться, когда одна из комнат опустеет.",
            ),
            ephemeral=True,
        )
        return True

    await interaction.followup.send(
        embed=embeds.info("Выберите деятельность", "От неё зависит название вашей комнаты."),
        view=_CallerActivityView(interaction.user.id, services),
        ephemeral=True,
    )
    return True


class _CallerActivityView(discord.ui.View):
    def __init__(self, owner_id: int, services: Services) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.services = services
        select = discord.ui.Select(
            placeholder="Вид деятельности",
            options=[discord.SelectOption(label=activity) for activity in CALLER_ACTIVITIES],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            embed=embeds.error("Не для тебя", "Этот выбор открыт для другого участника."), ephemeral=True
        )
        return False

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select = self.children[0]
        if not isinstance(select, discord.ui.Select) or not select.values:
            return
        await _join_caller(self.services, interaction, select.values[0])


def _caller_room_access(
    guild: discord.Guild, config: Any
) -> tuple[list[discord.Role], dict[Any, discord.PermissionOverwrite] | None]:
    """Роли, которым открыта комната коллера, и права на её создание.

    Без явного запрета для @everyone канал наследует права категории, и в
    комнату попадёт любой, кто её видит. Права отдаются словарём: Discord
    ждёт Mapping, и список кортежей отклоняется с TypeError.

    Если роли не заданы или не найдены, возвращается None: тогда параметр не
    передаётся вовсе и канал наследует категорию, как до этого изменения.
    """
    allowed: list[discord.Role] = []
    missing: list[int] = []
    for role_id in config.where_play_caller_role_ids:
        role = guild.get_role(role_id)
        if role is None:
            missing.append(role_id)
            continue
        allowed.append(role)

    if not allowed:
        _warn_missing_caller_roles(guild, missing, config)
        return [], None

    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False, speak=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True, move_members=True, manage_channels=True
        ),
    }
    for role in allowed:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
    return allowed, overwrites


#: Серверы, для которых уже перечислили роли в лог: иначе каждое нажатие
#: «Я коллер» засоряло бы лог одним и тем же списком.
_CALLER_ROLES_REPORTED: set[int] = set()


def _warn_missing_caller_roles(guild: discord.Guild, missing: list[int], config: Any) -> None:
    """Рассказывает, каких ролей не хватает, и один раз показывает доступные.

    Без списка доступных ролей невозможно понять, откуда взялся неверный ID.
    """
    if guild.id in _CALLER_ROLES_REPORTED:
        logger.warning(
            "Комната коллера на сервере %s без ограничения: роли из %s не найдены",
            guild.id,
            config.where_play_caller_role_ids,
        )
        return
    _CALLER_ROLES_REPORTED.add(guild.id)
    available = ", ".join(
        f"{role.id}={role.name}" for role in sorted(guild.roles, key=lambda item: item.position)
    ) or "нет"
    logger.warning(
        "Роли для комнаты коллера не найдены на сервере %s: %s (в конфиге: %s). "
        "Комната создаётся без ограничения доступа. Роли сервера: %s",
        guild.id,
        missing,
        config.where_play_caller_role_ids,
        available,
    )


async def _join_caller(services: Services, interaction: discord.Interaction, activity: str) -> None:
    if activity not in CALLER_ACTIVITIES or interaction.guild is None:
        await interaction.response.send_message(embed=embeds.error("Неверный выбор", "Выберите деятельность из списка."), ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member) or interaction.user.voice is None:
        return
    category = interaction.guild.get_channel(interaction.client.config.temp_voice_category_id or 0)
    if not isinstance(category, discord.CategoryChannel):
        await interaction.response.send_message(embed=embeds.error("Не настроено", "Категория голосовых комнат не задана."), ephemeral=True)
        return

    # temp_voices ключуется по owner_id глобально, поэтому чужая комната на
    # другом сервере помешала бы создать новую (конфликт первичного ключа).
    room_id = await services.tempvoice.channel_of_owner(interaction.user.id)
    room = interaction.guild.get_channel(room_id) if room_id else None
    if room_id is not None and not isinstance(room, discord.VoiceChannel):
        await interaction.response.send_message(
            embed=embeds.error(
                "Комната на другом сервере",
                "У вас уже есть временная комната на другом сервере. Освободите её — "
                "перенос между серверами Discord не поддерживает.",
            ),
            ephemeral=True,
        )
        return

    name = f"{activity} - {interaction.user.display_name}"[:100]
    created = False
    if isinstance(room, discord.VoiceChannel):
        if room.name != name:
            try:
                await room.edit(name=name, reason="Где играем: смена деятельности")
            except discord.HTTPException:
                pass
    else:
        allowed_roles, overwrites = _caller_room_access(interaction.guild, interaction.client.config)
        if allowed_roles:
            # Комната закрыта по ролям, поэтому без нужной роли в неё не войти,
            # и бот не сможет перенести туда нажавшего. Лучше отказать сразу,
            # чем создать комнату, в которую никто не сможет зайти.
            member_role_ids = {role.id for role in interaction.user.roles}
            if not any(role.id in member_role_ids for role in allowed_roles):
                names = ", ".join(discord.utils.escape_markdown(role.name) for role in allowed_roles)
                await interaction.response.send_message(
                    embed=embeds.error(
                        "Нет доступа к комнате",
                        f"Комната коллера доступна только участникам с ролями: {names}.",
                    ),
                    ephemeral=True,
                )
                return
        # Ограничений нет — параметр не передаём вовсе, иначе канал получит
        # пустой набор прав вместо наследования категории.
        extra: dict[str, Any] = {"overwrites": overwrites} if overwrites is not None else {}
        try:
            room = await interaction.guild.create_voice_channel(
                name,
                category=category,
                reason="Где играем: комната коллера",
                user_limit=MAX_CALLERS + 5,
                **extra,
            )
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                embed=embeds.error("Не удалось создать комнату", f"Discord отклонил запрос: {exc}"), ephemeral=True
            )
            return
        created = True
        try:
            await services.tempvoice.create(interaction.user.id, room.id, datetime.now(UTC))
        except Exception:
            logger.exception("Где играем: не удалось сохранить комнату #%s", room.id)
            await room.delete(reason="Где играем: не удалось сохранить комнату")
            await interaction.response.send_message(
                embed=embeds.error("Не удалось сохранить комнату", "Попробуйте ещё раз чуть позже."), ephemeral=True
            )
            return

    try:
        await interaction.user.move_to(room, reason="Где играем: вход в комнату коллера")
    except discord.HTTPException as exc:
        if created:
            with contextlib.suppress(discord.HTTPException):
                await room.delete(reason="Не удалось перенести коллера")
            await services.tempvoice.delete(room.id)
        await interaction.response.send_message(
            embed=embeds.error("Не удалось перенести", f"Бот не смог переместить вас: {exc}"), ephemeral=True
        )
        return

    await services.where_play.remember_caller_room(interaction.guild_id, interaction.user.id, room.id)
    row = await services.where_play.row(interaction.guild_id)
    if row is not None:
        with contextlib.suppress(discord.HTTPException):
            await services.where_play.publish(row)
    await interaction.response.send_message(
        embed=embeds.success("Комната готова", f"Вы в {room.mention}. Список коллеров обновится в карточке."),
        ephemeral=True,
    )


class TicketCloseView(_TicketBaseView):
    """Устойчивая кнопка закрытия тикета (работает после рестарта)."""

    def __init__(
        self,
        ticket_service: TicketService,
        *,
        label: str = "Закрыть тикет",
        emoji: str | None = "🔒",
    ) -> None:
        super().__init__(ticket_service)
        self.close_ticket.label = label
        self.close_ticket.emoji = emoji or None

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id=TICKET_CLOSE_ID, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error("Не удалось закрыть тикет", "Кнопка работает только на сервере."),
                ephemeral=True,
            )
            return
        channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        result = await self.ticket_service.close(interaction.guild, channel, interaction.user)
        if result.error:
            embed = embeds.error("Не удалось закрыть тикет", result.error)
        else:
            embed = embeds.success("Тикет закрыт", result.transcript_channel_mention)
        await interaction.response.send_message(embed=embed, ephemeral=True)
