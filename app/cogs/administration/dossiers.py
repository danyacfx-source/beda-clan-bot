"""Команды личных дел: настройка форума, допуск после собеседования, проверка анкеты."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.services.dossier_service import (
    GEAR,
    GEAR_NOTE,
    SPECIALIZATIONS,
    DossierPermissionError,
    DossierService,
    DossierValidationError,
    available_gear,
    gear_embed,
    normalize_specialization,
    profile_embed,
    review_card,
    specialization_asset,
    validate_dossier,
)

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.dossiers")

DOSSIER_FILL_ID = "beda:dossier:form:v1"
DOSSIER_APPROVE_ID = "beda:approve:v2"
DOSSIER_SELF_ID = "beda:dossier:self:v1"

ROLE_ARGUMENTS = {
    "beda": "Роль BEDA",
    "assault": "Штурмовик",
    "medic": "Медик",
    "support": "Поддержка",
    "pilot": "Пилот",
    "driver": "Водитель",
    "sniper": "Снайпер",
}


def _error_text(error: DossierValidationError | DossierPermissionError) -> str:
    return error.user_message


# --- интерактивная карточка личного дела ---

TAB_LABELS = {"profile": "Профиль", "gear": "Снаряжение", "edit": "Редактирование"}
TAB_ID = "beda:dossier:tab:{tab}:{owner}"
EDIT_DENIED = "Редактировать может только владелец дела или администратор."


def _service(interaction: discord.Interaction) -> DossierService:
    services = interaction.client.services  # type: ignore[attr-defined]
    return services.dossiers


def _updated_by_name(guild: discord.Guild | None, data: dict[str, Any]) -> str | None:
    actor = data.get("updated_by")
    if not actor:
        return None
    member = guild.get_member(int(actor)) if guild is not None else None
    return member.display_name if member else None


def _tab_embed(guild: discord.Guild | None, data: dict[str, Any], owner_id: int, tab: str) -> discord.Embed:
    if tab == "gear":
        return gear_embed(data, owner_id)
    return profile_embed(data, owner_id, _updated_by_name(guild, data))


class TabButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"beda:dossier:tab:(?P<tab>profile|gear|edit):(?P<owner>[0-9]+)",
):
    """Вкладка карточки. custom_id несёт владельца дела, поэтому переживает перезапуск."""

    def __init__(self, tab: str, owner_id: int, *, active: bool = False) -> None:
        self.tab = tab
        self.owner_id = owner_id
        super().__init__(
            discord.ui.Button(
                label=TAB_LABELS[tab],
                style=discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary,
                custom_id=TAB_ID.format(tab=tab, owner=owner_id),
                row=0,
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str], /
    ) -> TabButton:
        return cls(match["tab"], int(match["owner"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        service = _service(interaction)
        if self.tab == "edit":
            await self._open_editor(interaction, service)
            return
        await interaction.response.defer()
        guild = interaction.guild
        try:
            data = await service.card(int(interaction.guild_id or 0), self.owner_id)
            if interaction.message is None:
                return
            await interaction.message.edit(
                embed=_tab_embed(guild, data, self.owner_id, self.tab),
                view=card_view(self.owner_id, self.tab),
            )
        except (DossierValidationError, DossierPermissionError) as error:
            await interaction.followup.send(embed=embeds.error("Личное дело", _error_text(error)), ephemeral=True)
        except Exception:
            logger.exception("Не удалось переключить вкладку личного дела")
            await interaction.followup.send(
                embed=embeds.error("Личное дело", "Не удалось открыть вкладку. Подробности в консоли бота."),
                ephemeral=True,
            )

    async def _open_editor(self, interaction: discord.Interaction, service: DossierService) -> None:
        if not await _may_edit(interaction, service, self.owner_id):
            await interaction.response.send_message(
                embed=embeds.error("Редактирование", EDIT_DENIED), ephemeral=True
            )
            return
        embed = embeds.info(
            "Редактирование личного дела",
            "Это ваше дело: вы можете обновить профиль и снаряжение.\n" + GEAR_NOTE,
        )
        await interaction.response.send_message(
            embed=embed, view=EditMenuView(service, self.owner_id), ephemeral=True
        )


def card_view(owner_id: int, active_tab: str) -> discord.ui.View:
    """Постоянная панель вкладок под карточкой личного дела."""
    view = discord.ui.View(timeout=None)
    for tab in TAB_LABELS:
        view.add_item(TabButton(tab, owner_id, active=tab == active_tab))
    return view


def dossier_dynamic_items() -> tuple[type[discord.ui.DynamicItem], ...]:
    """Динамические кнопки для ``bot.add_dynamic_items`` (регистрируются при старте)."""
    return (TabButton,)


async def _may_edit(interaction: discord.Interaction, service: DossierService, owner_id: int) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and await service.can_edit(user, owner_id)


async def refresh_card(
    guild: discord.Guild,
    service: DossierService,
    owner_id: int,
    data: dict[str, Any],
    *,
    tab: str,
    reattach: bool,
) -> None:
    """Перерисовать стартовое сообщение треда: embed вкладки, кнопки и (при смене роли) картинку."""
    thread_id = await service.thread_id(guild.id, owner_id)
    if thread_id is None:
        raise DossierValidationError("У личного дела нет треда.")
    thread = guild.get_channel_or_thread(thread_id) or await guild.fetch_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        raise DossierValidationError("Тред личного дела не найден.")
    message = await thread.fetch_message(thread.id)  # у форумного треда id стартового сообщения == id треда
    kwargs: dict[str, Any] = {
        "embed": _tab_embed(guild, data, owner_id, tab),
        "view": card_view(owner_id, tab),
    }
    file: discord.File | None = None
    if reattach:
        primary = normalize_specialization(data.get("primary")) or str(data.get("primary"))
        file = discord.File(specialization_asset(primary), filename="specialization.png")
        kwargs["attachments"] = [file]
    try:
        await message.edit(**kwargs)
    finally:
        if file is not None:
            file.close()


async def _apply_update(
    interaction: discord.Interaction,
    service: DossierService,
    owner_id: int,
    update: Callable[[], Awaitable[dict[str, Any]]],
    *,
    tab: str,
    title: str,
    sync_roles: bool = False,
) -> None:
    """Общий конвейер сохранения: права → сервис → обновление карточки → ответ."""
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if guild is None:
        return
    if not await _may_edit(interaction, service, owner_id):
        await interaction.followup.send(embed=embeds.error(title, EDIT_DENIED), ephemeral=True)
        return
    try:
        old = await service.card(guild.id, owner_id)
        data = await update()
    except (DossierValidationError, DossierPermissionError) as error:
        await interaction.followup.send(embed=embeds.error(title, _error_text(error)), ephemeral=True)
        return
    except Exception:
        logger.exception("Не удалось сохранить личное дело")
        await interaction.followup.send(
            embed=embeds.error(title, "Не удалось сохранить изменения. Подробности в консоли бота."),
            ephemeral=True,
        )
        return
    changed_role = normalize_specialization(old.get("primary")) != normalize_specialization(data.get("primary"))
    try:
        await refresh_card(guild, service, owner_id, data, tab=tab, reattach=changed_role)
    except Exception:
        logger.warning("Не удалось обновить карточку личного дела owner=%s", owner_id, exc_info=True)
    text = "Изменения сохранены."
    if sync_roles and not await _sync_owner_roles(guild, service, owner_id, data):
        text += "\nРоли не обновлены — обратитесь к администратору."
    await interaction.followup.send(embed=embeds.success(title, text), ephemeral=True)


async def _sync_owner_roles(
    guild: discord.Guild, service: DossierService, owner_id: int, data: dict[str, Any]
) -> bool:
    """Роли специализаций выдаются владельцу дела (не редактору). Ушедший с сервера — не ошибка."""
    try:
        member = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
    except discord.NotFound:
        return True
    except discord.HTTPException:
        logger.warning("Не удалось получить владельца дела owner=%s", owner_id, exc_info=True)
        return False
    return await service.sync_specialization_roles(guild, member, data)


class ProfileModal(discord.ui.Modal, title="Редактировать профиль"):
    def __init__(self, service: DossierService, owner_id: int, data: dict[str, Any]) -> None:
        super().__init__()
        self.service = service
        self.owner_id = owner_id
        roles = ", ".join(SPECIALIZATIONS)
        self.nickname = discord.ui.TextInput(
            label="Позывной", max_length=60, default=str(data.get("nickname") or "")
        )
        self.season = discord.ui.TextInput(
            label="Играет с сезона",
            required=False,
            max_length=60,
            placeholder="Первый сезон участия",
            default=str(data.get("season") or "") or None,
        )
        self.primary = discord.ui.TextInput(
            label="Основная роль",
            max_length=30,
            placeholder=roles,
            default=normalize_specialization(data.get("primary")) or str(data.get("primary") or ""),
        )
        self.secondary = discord.ui.TextInput(
            label="Дополнительная роль",
            max_length=30,
            placeholder=roles,
            default=normalize_specialization(data.get("secondary")) or str(data.get("secondary") or ""),
        )
        for item in (self.nickname, self.season, self.primary, self.secondary):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _apply_update(
            interaction,
            self.service,
            self.owner_id,
            lambda: self.service.update_profile(
                int(interaction.guild_id or 0),
                self.owner_id,
                int(interaction.user.id),
                nickname=str(self.nickname),
                season=str(self.season),
                primary=str(self.primary),
                secondary=str(self.secondary),
            ),
            tab="profile",
            title="Профиль",
            sync_roles=True,
        )


class IdentityModal(discord.ui.Modal, title="Имя, Steam и дата"):
    def __init__(self, service: DossierService, owner_id: int, data: dict[str, Any]) -> None:
        super().__init__()
        self.service = service
        self.owner_id = owner_id
        joined = ""
        if data.get("joined_at"):
            joined = _format_date(str(data["joined_at"]))
        self.name = discord.ui.TextInput(
            label="Имя человека",
            max_length=60,
            required=data.get("source") != "direct",
            default=str(data.get("name") or "") or None,
        )
        self.steam_url = discord.ui.TextInput(
            label="Ссылка на Steam-профиль",
            required=False,
            max_length=200,
            placeholder="https://steamcommunity.com/id/...",
            default=str(data.get("steam_url") or "") or None,
        )
        self.joined_at = discord.ui.TextInput(
            label="Дата вступления в клан",
            required=False,
            max_length=10,
            placeholder="ДД.ММ.ГГГГ",
            default=joined or None,
        )
        for item in (self.name, self.steam_url, self.joined_at):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _apply_update(
            interaction,
            self.service,
            self.owner_id,
            lambda: self.service.update_identity(
                int(interaction.guild_id or 0),
                self.owner_id,
                int(interaction.user.id),
                name=str(self.name),
                steam_url=str(self.steam_url),
                joined_at=str(self.joined_at),
            ),
            tab="profile",
            title="Имя, Steam и дата",
        )


def _format_date(iso: str) -> str:
    parts = iso.split("-")
    return ".".join(reversed(parts)) if len(parts) == 3 else iso


class EditMenuView(discord.ui.View):
    """Эфемерное меню редактирования: открывает модалки и выбор снаряжения."""

    def __init__(self, service: DossierService, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.owner_id = owner_id

    async def _guard(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        """Права перепроверяются на каждом шаге, а не только при открытии меню."""
        if not await _may_edit(interaction, self.service, self.owner_id):
            await interaction.response.send_message(embed=embeds.error("Редактирование", EDIT_DENIED), ephemeral=True)
            return None
        try:
            return await self.service.card(int(interaction.guild_id or 0), self.owner_id)
        except DossierValidationError as error:
            await interaction.response.send_message(
                embed=embeds.error("Редактирование", _error_text(error)), ephemeral=True
            )
            return None
        except Exception:
            logger.exception("Не удалось прочитать личное дело")
            await interaction.response.send_message(
                embed=embeds.error("Редактирование", "Не удалось прочитать личное дело. Подробности в консоли бота."),
                ephemeral=True,
            )
            return None

    @discord.ui.button(label="Изменить профиль", style=discord.ButtonStyle.primary)
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        data = await self._guard(interaction)
        if data is not None:
            await interaction.response.send_modal(ProfileModal(self.service, self.owner_id, data))

    @discord.ui.button(label="Имя, Steam и дата", style=discord.ButtonStyle.secondary)
    async def identity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        data = await self._guard(interaction)
        if data is not None:
            await interaction.response.send_modal(IdentityModal(self.service, self.owner_id, data))

    @discord.ui.button(label="Отметить снаряжение", style=discord.ButtonStyle.secondary)
    async def gear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        data = await self._guard(interaction)
        if data is None:
            return
        await interaction.response.send_message(
            embed=embeds.info("Выберите фактически открытые предметы", GEAR_NOTE),
            view=GearSelectView(self.service, self.owner_id, data),
            ephemeral=True,
        )


class GearSelect(discord.ui.Select):
    async def callback(self, interaction: discord.Interaction) -> None:
        view: GearSelectView = self.view  # type: ignore[assignment]
        view.selected = list(self.values)
        for option in self.options:
            option.default = option.value in view.selected
        await interaction.response.edit_message(view=view)


class GearSelectView(discord.ui.View):
    def __init__(self, service: DossierService, owner_id: int, data: dict[str, Any]) -> None:
        super().__init__(timeout=900)
        self.service = service
        self.owner_id = owner_id
        owned = set(data.get("gear") or [])
        role_of = {item: role for role, items in GEAR.items() for item in items}
        items = available_gear()
        self.selected = [item for item in items if item in owned]
        select = GearSelect(
            placeholder="Открытое снаряжение",
            min_values=0,
            max_values=len(items),
            options=[
                discord.SelectOption(label=item, value=item, description=role_of.get(item), default=item in owned)
                for item in items
            ],
        )
        self.add_item(select)

    @discord.ui.button(label="Применить", style=discord.ButtonStyle.primary, row=1)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _apply_update(
            interaction,
            self.service,
            self.owner_id,
            lambda: self.service.update_gear(
                int(interaction.guild_id or 0), self.owner_id, int(interaction.user.id), self.selected
            ),
            tab="gear",
            title="Снаряжение",
        )
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Отменено.", embed=None, view=None)
        self.stop()


class DossierModal(discord.ui.Modal, title="Личное дело"):
    """Анкета заявителя. Две специализации выбираются следующим шагом."""

    nickname = discord.ui.TextInput(label="Ник", max_length=60)
    name = discord.ui.TextInput(label="Имя", max_length=60)
    city = discord.ui.TextInput(label="Город", max_length=60)
    steam_id = discord.ui.TextInput(label="Steam ID", max_length=100)

    def __init__(self, service: DossierService) -> None:
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction) -> None:
        data: dict[str, Any] = {
            "nickname": str(self.nickname),
            "name": str(self.name),
            "city": str(self.city),
            "steam_id": str(self.steam_id),
        }
        await interaction.response.send_message(
            "Выберите две специализации и отправьте анкету администратору.",
            view=SpecializationView(data, self.service),
            ephemeral=True,
        )


class SpecializationSelect(discord.ui.Select):
    def __init__(self, key: str, label: str) -> None:
        super().__init__(
            placeholder=label,
            options=[discord.SelectOption(label=value) for value in SPECIALIZATIONS],
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.data[self.key] = self.values[0]  # type: ignore[attr-defined]
        for option in self.options:
            option.default = option.value == self.values[0]
        await interaction.response.edit_message(view=self.view)


class SpecializationView(discord.ui.View):
    def __init__(self, data: dict[str, Any], service: DossierService) -> None:
        super().__init__(timeout=900)
        self.data = data
        self.service = service
        self.add_item(SpecializationSelect("primary", "Основная специализация"))
        self.add_item(SpecializationSelect("secondary", "Дополнительная специализация"))

    @discord.ui.button(label="Отправить анкету", style=discord.ButtonStyle.success, row=2)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = validate_dossier(dict(self.data))
            await self.service.submit_draft(
                int(interaction.guild_id or 0),
                int(interaction.user.id),
                int(interaction.channel_id or 0),
                data,
            )
        except (DossierValidationError, DossierPermissionError) as error:
            await interaction.followup.send(embed=embeds.error("Анкета", _error_text(error)), ephemeral=True)
            return
        except Exception:
            logger.exception("Не удалось сохранить анкету")
            await interaction.followup.send(
                embed=embeds.error("Анкета", "Не удалось сохранить анкету. Подробности в консоли бота."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=embeds.success("Анкета сохранена", "Она передана администратору на проверку."),
            ephemeral=True,
        )
        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.send(
                "Анкета личного дела заполнена. Администратор: откройте /review_dossier в этом тикете.",
                allowed_mentions=discord.AllowedMentions.none(),
            )


class AdmissionView(discord.ui.View):
    """Кнопка «заполнить личное дело» в тикете одобренного заявителя."""

    def __init__(self, service: DossierService) -> None:
        super().__init__(timeout=None)
        self.service = service

    @discord.ui.button(
        label="Заполнить личное дело",
        style=discord.ButtonStyle.primary,
        custom_id=DOSSIER_FILL_ID,
    )
    async def fill(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        reason = await self.service.form_block_reason(
            int(interaction.guild_id or 0),
            int(interaction.user.id),
            int(interaction.channel_id or 0),
        )
        if reason:
            await interaction.response.send_message(
                embed=embeds.error("Анкета недоступна", reason),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(DossierModal(self.service))


class ApprovalView(discord.ui.View):
    """Кнопка «успешное собеседование» в тикете заявителя."""

    def __init__(self, service: DossierService) -> None:
        super().__init__(timeout=None)
        self.service = service

    @discord.ui.button(
        label="Успешное собеседование",
        style=discord.ButtonStyle.success,
        custom_id=DOSSIER_APPROVE_ID,
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._approve(interaction)

    async def _approve(self, interaction: discord.Interaction) -> bool:
        owner_id = await self.service.ticket_owner(
            int(interaction.guild_id or 0),
            int(interaction.channel_id or 0),
        )
        if owner_id is None:
            await interaction.followup.send(
                embed=embeds.error("Нужен открытый тикет", "Откройте команду в канале открытого тикета."),
                ephemeral=True,
            )
            return False
        try:
            await self.service.approve(int(interaction.guild_id or 0), owner_id, int(interaction.channel_id or 0))
        except (DossierValidationError, DossierPermissionError) as error:
            await interaction.followup.send(embed=embeds.error("Анкета", _error_text(error)), ephemeral=True)
            return False
        except Exception:
            logger.exception("Не удалось одобрить собеседование")
            await interaction.followup.send(
                embed=embeds.error("Анкета", "Не удалось одобрить собеседование. Подробности в консоли бота."),
                ephemeral=True,
            )
            return False
        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.send(
                f"<@{owner_id}>, собеседование пройдено. Заполните личное дело.",
                view=AdmissionView(self.service),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await interaction.followup.send(
            embed=embeds.success("Готово", "Анкета доступна заявителю по кнопке в тикете."),
            ephemeral=True,
        )
        return True


class AdminCreateView(discord.ui.View):
    def __init__(self, owner_id: int, revision: int, service: DossierService) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.revision = revision
        self.service = service

    @discord.ui.button(label="Создать личное дело", style=discord.ButtonStyle.success)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return
        try:
            outcome = await self.service.publish(
                guild,
                self.owner_id,
                int(interaction.channel_id or 0),
                self.revision,
            )
        except (DossierValidationError, DossierPermissionError) as error:
            await interaction.followup.send(embed=embeds.error("Публикация", _error_text(error)), ephemeral=True)
            return
        except Exception:
            logger.exception("Не удалось опубликовать личное дело")
            await interaction.followup.send(
                embed=embeds.error("Публикация", "Не удалось опубликовать личное дело. Подробности в консоли бота."),
                ephemeral=True,
            )
            return
        if outcome == "abandoned":
            await interaction.followup.send(
                embed=embeds.error(
                    "Заявитель покинул сервер",
                    "Тред создан, запись помечена как abandoned. Роли выдайте вручную.",
                ),
                ephemeral=True,
            )
            return
        if outcome == "roles_pending":
            await interaction.followup.send(
                embed=embeds.error(
                    "Роли не выданы",
                    "Тред создан, статус сохранён как roles_pending. Выдайте роли вручную.",
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=embeds.success("Личное дело создано", "Тред опубликован, роли выданы."),
            ephemeral=True,
        )


DIRECT_HINT = "Остальное заполните через «Редактирование» на карточке."


async def _send_direct_result(
    interaction: discord.Interaction, title: str, outcome: str, thread_id: int
) -> None:
    if outcome == "roles_pending":
        await interaction.followup.send(
            embed=embeds.error(
                "Роли не выданы",
                f"Дело создано: <#{thread_id}>, но бот не смог выдать роли. Выдайте роли BEDA и специализаций вручную.",
            ),
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        embed=embeds.success(title, f"<#{thread_id}>\n{DIRECT_HINT}"), ephemeral=True
    )


async def _send_failure(interaction: discord.Interaction, title: str, error: Exception) -> None:
    if isinstance(error, DossierValidationError | DossierPermissionError):
        embed = embeds.error(title, _error_text(error))
    else:
        logger.exception("Не удалось создать личное дело", exc_info=error)
        embed = embeds.error(title, "Не удалось создать личное дело. Подробности в консоли бота.")
    await interaction.followup.send(embed=embed, ephemeral=True)


class _SelfRoleSelect(discord.ui.Select):
    def __init__(self, key: str, placeholder: str, row: int) -> None:
        super().__init__(
            placeholder=placeholder,
            options=[discord.SelectOption(label=value) for value in SPECIALIZATIONS],
            row=row,
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SelfDossierView = self.view  # type: ignore[assignment]
        view.chosen[self.key] = self.values[0]
        for option in self.options:
            option.default = option.value == self.values[0]
        await interaction.response.edit_message(view=view)


class SelfDossierView(discord.ui.View):
    """Эфемерный выбор двух ролей для самостоятельного создания дела."""

    def __init__(self, service: DossierService) -> None:
        super().__init__(timeout=600)
        self.service = service
        self.chosen: dict[str, str] = {}
        self._busy = False
        self.add_item(_SelfRoleSelect("primary", "Основная роль", 0))
        self.add_item(_SelfRoleSelect("secondary", "Дополнительная роль", 1))

    @discord.ui.button(label="Создать", style=discord.ButtonStyle.success, row=2)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member) or self._busy:
            return
        self._busy = True
        try:
            reason = await self.service.self_create_block_reason(guild, member, throttle=False)
            if reason:
                self._busy = False
                await interaction.followup.send(embed=embeds.error("Личное дело", reason), ephemeral=True)
                return
            primary, secondary = self.chosen.get("primary"), self.chosen.get("secondary")
            if not primary or not secondary:
                self._busy = False
                await interaction.followup.send(
                    embed=embeds.error("Личное дело", "Выберите основную и дополнительную роли."), ephemeral=True
                )
                return
            if primary == secondary:
                self._busy = False
                await interaction.followup.send(
                    embed=embeds.error("Личное дело", "Основная и дополнительная роли должны различаться."),
                    ephemeral=True,
                )
                return
            outcome, thread_id = await self.service.create_direct(guild, member, primary, secondary, member.id)
        except Exception as error:
            self._busy = False
            await _send_failure(interaction, "Личное дело", error)
            return
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        self.stop()
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException:
            logger.debug("Не удалось отключить панель выбора ролей", exc_info=True)
        await _send_direct_result(interaction, "Личное дело создано", outcome, thread_id)


class SelfDossierPanelView(discord.ui.View):
    """Публичная панель: участник клана сам создаёт себе личное дело."""

    def __init__(self, service: DossierService) -> None:
        super().__init__(timeout=None)
        self.service = service

    @discord.ui.button(
        label="Создать своё личное дело",
        style=discord.ButtonStyle.success,
        custom_id=DOSSIER_SELF_ID,
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error("Личное дело", "Кнопка работает только на сервере."), ephemeral=True
            )
            return
        try:
            reason = await self.service.self_create_block_reason(guild, member)
        except Exception:
            logger.exception("Не удалось проверить доступ к самостоятельному созданию дела")
            reason = "Не удалось проверить доступ. Подробности в консоли бота."
        if reason:
            await interaction.response.send_message(embed=embeds.error("Личное дело", reason), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.info("Создание личного дела", "Выберите основную и дополнительную роль, затем нажмите «Создать»."),
            view=SelfDossierView(self.service),
            ephemeral=True,
        )


RESET_ROLES_NOTE = (
    "Роли специализаций сняты. Чтобы вернуть их, участнику нужно открыть "
    "«Редактирование → Изменить профиль» на своей карточке и сохранить (можно без изменений)."
)


class ResetConfirmView(discord.ui.View):
    """Подтверждение сброса личных дел: нажать может только вызвавший администратор."""

    def __init__(self, cog: DossierCog, invoker_id: int, interaction: discord.Interaction | None = None) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.invoker_id = invoker_id
        self.interaction = interaction
        self._claimed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        allowed = (
            int(user.id) == self.invoker_id
            and isinstance(user, discord.Member)
            and user.guild_permissions.administrator
        )
        if not allowed:
            await interaction.response.send_message(
                embed=embeds.error("Сброс личных дел", "Подтвердить может только вызвавший команду администратор."),
                ephemeral=True,
            )
        return allowed

    def _claim(self) -> bool:
        """Синхронно занять действие: повторное нажатие получает False."""
        if self._claimed:
            return False
        self._claimed = True
        self.stop()
        return True

    async def _disable(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        try:
            await interaction.edit_original_response(view=self)
        except discord.HTTPException:
            logger.debug("Не удалось отключить панель сброса", exc_info=True)

    async def on_timeout(self) -> None:
        if self._claimed or self.interaction is None:
            return
        await self._disable(self.interaction)

    @discord.ui.button(label="Сбросить", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        claimed = self._claim()
        await interaction.response.defer(ephemeral=True)
        if not claimed:
            return
        await self._disable(interaction)
        guild = interaction.guild
        if guild is None:
            return
        await self.cog.run_reset(interaction, guild)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        claimed = self._claim()
        await interaction.response.defer(ephemeral=True)
        if not claimed:
            return
        await self._disable(interaction)
        await interaction.followup.send(embed=embeds.info("Сброс личных дел", "Отменено."), ephemeral=True)


class DossierCog(ClanCog, name="Dossiers"):
    def __init__(self, bot: ClanBot, dossiers: DossierService) -> None:
        super().__init__(bot)
        self.dossiers = dossiers

    async def cog_unload(self) -> None:
        await self.dossiers.aclose()

    def views(self) -> list[discord.ui.View]:
        """Persistent views: обе кнопки живут в тикете и должны пережить перезапуск."""
        return [AdmissionView(self.dossiers), ApprovalView(self.dossiers), SelfDossierPanelView(self.dossiers)]

    @app_commands.command(
        name="dossier_add",
        description="Создать личное дело действующему участнику клана",
    )
    @app_commands.describe(
        member="Участник клана",
        primary="Основная роль",
        secondary="Дополнительная роль",
    )
    @app_commands.choices(
        primary=[app_commands.Choice(name=name, value=name) for name in SPECIALIZATIONS],
        secondary=[app_commands.Choice(name=name, value=name) for name in SPECIALIZATIONS],
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def dossier_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        primary: app_commands.Choice[str],
        secondary: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return
        if member.bot:
            await interaction.followup.send(
                embed=embeds.error("Личное дело", "Личное дело нельзя создать для бота."), ephemeral=True
            )
            return
        try:
            outcome, thread_id = await self.dossiers.create_direct(
                guild, member, primary.value, secondary.value, interaction.user.id
            )
        except Exception as error:
            await _send_failure(interaction, "Личное дело", error)
            return
        await _send_direct_result(interaction, "Личное дело создано", outcome, thread_id)

    @app_commands.command(
        name="dossier_reset",
        description="Сбросить личные дела для нового сезона (снаряжение и роли специализаций)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def dossier_reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return
        try:
            count = await self.dossiers.dossier_count(guild.id)
        except Exception:
            logger.exception("Не удалось посчитать личные дела")
            await interaction.followup.send(
                embed=embeds.error("Сброс личных дел", "Не удалось прочитать дела. Подробности в консоли бота."),
                ephemeral=True,
            )
            return
        embed = embeds.warning(
            "Сброс личных дел",
            f"Будет снято снаряжение у всех дел ({count}) и роли специализаций у всех участников сервера.\n"
            "Треды, профили, специализации в карточках и роль BEDA останутся.\n"
            + RESET_ROLES_NOTE
            + "\n**Действие необратимо.**",
        )
        view = ResetConfirmView(self, int(interaction.user.id), interaction)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def run_reset(self, interaction: discord.Interaction, guild: discord.Guild) -> None:
        """Выполнить сброс и перерисовать карточки; итог отправляется follow-up'ом."""
        try:
            report = await self.dossiers.reset_all(guild, int(interaction.user.id))
        except (DossierValidationError, DossierPermissionError) as error:
            await self._safe_send(interaction, embeds.error("Сброс личных дел", _error_text(error)))
            return
        except Exception:
            logger.exception("Не удалось выполнить сброс личных дел")
            await self._safe_send(
                interaction,
                embeds.error(
                    "Сброс личных дел",
                    "Сброс прерван, часть дел могла быть сброшена — повторите команду, это безопасно.",
                ),
            )
            return
        for owner_id in report.card_owners:
            try:
                data = await self.dossiers.card(guild.id, owner_id)
                await refresh_card(guild, self.dossiers, owner_id, data, tab="profile", reattach=False)
                report.cards_refreshed += 1
            except Exception:
                report.cards_failed += 1
                logger.warning("Не удалось обновить карточку после сброса owner=%s", owner_id, exc_info=True)
        lines = [
            f"Дел сброшено: {report.dossiers_reset}",
            f"Дел не удалось сбросить: {report.dossiers_failed}",
            f"Роли сняты у участников: {report.roles_removed_members}",
            f"Не удалось снять роли у участников: {report.roles_failed_members}",
            f"Карточек обновлено: {report.cards_refreshed}",
            f"Карточек не обновлено: {report.cards_failed}",
        ]
        if report.roles_missing:
            lines.append("Роли специализаций не найдены на сервере — проверьте /setup_dossiers")
        if report.members_incomplete:
            lines.append("Не удалось загрузить всех участников: часть ролей могла остаться — повторите команду.")
        lines.append(RESET_ROLES_NOTE)
        problems = report.dossiers_failed or report.roles_failed_members or report.roles_missing
        build = embeds.error if problems and not report.dossiers_reset else embeds.success
        await self._safe_send(interaction, build("Сброс завершён", "\n".join(lines)))

    @staticmethod
    async def _safe_send(interaction: discord.Interaction, embed: discord.Embed) -> None:
        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Не удалось отправить итог сброса личных дел", exc_info=True)

    @app_commands.command(
        name="dossier_panel",
        description="Опубликовать панель самостоятельного создания личных дел",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def dossier_panel(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error("Панель", "Команду нужно вызвать в текстовом канале."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(
            title="Личные дела BEDA",
            description=(
                "Уже состоите в клане? Создайте своё личное дело: выберите основную и дополнительную роль, "
                "остальное заполните через «Редактирование» на карточке."
            ),
            color=discord.Color(0xB89960),
        )
        try:
            await channel.send(embed=embed, view=SelfDossierPanelView(self.dossiers))
        except discord.HTTPException:
            logger.exception("Не удалось опубликовать панель личных дел")
            await interaction.followup.send(
                embed=embeds.error("Панель", "Не удалось отправить сообщение. Проверьте права бота в канале."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=embeds.success("Панель опубликована", channel.mention), ephemeral=True)

    @app_commands.command(
        name="dossier_card",
        description="Обновить карточку личного дела участника (добавить кнопки)",
    )
    @app_commands.describe(member="Участник, чьё личное дело нужно обновить")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def dossier_card(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return
        try:
            data = await self.dossiers.card(guild.id, member.id)
            await refresh_card(guild, self.dossiers, member.id, data, tab="profile", reattach=True)
        except (DossierValidationError, DossierPermissionError) as error:
            await interaction.followup.send(embed=embeds.error("Личное дело", _error_text(error)), ephemeral=True)
            return
        except Exception:
            logger.exception("Не удалось обновить карточку личного дела")
            await interaction.followup.send(
                embed=embeds.error("Личное дело", "Не удалось обновить карточку. Подробности в консоли бота."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=embeds.success("Карточка обновлена", f"Личное дело {member.mention} обновлено."),
            ephemeral=True,
        )

    @app_commands.command(
        name="setup_dossiers",
        description="Настроить форум личных дел и роли",
    )
    @app_commands.describe(
        forum="Форум, где будут публиковаться личные дела",
        **ROLE_ARGUMENTS,
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_dossiers(
        self,
        interaction: discord.Interaction,
        forum: discord.ForumChannel,
        beda: discord.Role,
        assault: discord.Role,
        medic: discord.Role,
        support: discord.Role,
        pilot: discord.Role,
        driver: discord.Role,
        sniper: discord.Role,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return
        mapping = dict(
            zip(
                ("BEDA", *SPECIALIZATIONS),
                (role.id for role in (beda, assault, medic, support, pilot, driver, sniper)),
                strict=True,
            )
        )
        try:
            await self.dossiers.setup(guild, forum, mapping)
        except (DossierValidationError, DossierPermissionError) as error:
            await interaction.followup.send(embed=embeds.error("Настройка", _error_text(error)), ephemeral=True)
            return
        except Exception:
            logger.exception("Не удалось сохранить настройки личных дел")
            await interaction.followup.send(
                embed=embeds.error("Настройка", "Не удалось сохранить настройки. Подробности в консоли бота."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=embeds.success(
                "Форум настроен",
                "\n".join(
                    f"{label} — {role.mention}"
                    for label, role in (
                        ("Форум", forum),
                        ("BEDA", beda),
                        *zip(SPECIALIZATIONS, (assault, medic, support, pilot, driver, sniper), strict=True),
                    )
                ),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="approve_interview",
        description="Открыть анкету личного дела в текущем тикете",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def approve_interview(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await ApprovalView(self.dossiers)._approve(interaction)

    @app_commands.command(
        name="review_dossier",
        description="Проверить анкету и создать личное дело заявителя",
    )
    @app_commands.describe(
        city="Уточнить город, если он не указан в анкете",
        steam_id="Уточнить Steam ID, если он не указан в анкете",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def review_dossier(
        self,
        interaction: discord.Interaction,
        city: str | None = None,
        steam_id: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            owner_id, data, revision, missing = await self.dossiers.prepare_review(
                int(interaction.guild_id or 0),
                int(interaction.channel_id or 0),
                city,
                steam_id,
            )
        except DossierValidationError as error:
            await interaction.followup.send(
                embed=embeds.error("Проверка", error.user_message),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Не удалось прочитать анкету")
            await interaction.followup.send(
                embed=embeds.error("Проверка", "Не удалось прочитать анкету. Подробности в консоли бота."),
                ephemeral=True,
            )
            return

        embed = review_card(data, owner_id)
        if missing:
            embed.set_footer(
                text="Дополните анкету: " + ", ".join(missing),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        await interaction.followup.send(
            embed=embed,
            view=AdminCreateView(owner_id, revision, self.dossiers),
            ephemeral=True,
        )
