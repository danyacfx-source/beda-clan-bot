"""Команды личных дел: настройка форума, допуск после собеседования, проверка анкеты."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import MegaCog
from app.services.dossier_service import (
    SPECIALIZATIONS,
    DossierPermissionError,
    DossierService,
    DossierValidationError,
    review_card,
    validate_dossier,
)

if TYPE_CHECKING:
    from app.core.bot import MegaBot

logger = logging.getLogger("bot.dossiers")

DOSSIER_FILL_ID = "beda:dossier:form:v1"
DOSSIER_APPROVE_ID = "beda:approve:v2"

ROLE_ARGUMENTS = {
    "beda": "Роль BEDA",
    "assault": "Штурмовик",
    "medic": "Медик",
    "mechanic": "Механик",
    "builder": "Строитель",
    "pilot": "Пилот",
    "sniper": "Снайпер",
}


def _error_text(error: DossierValidationError | DossierPermissionError) -> str:
    return error.user_message


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


class DossierCog(MegaCog, name="Dossiers"):
    def __init__(self, bot: MegaBot, dossiers: DossierService) -> None:
        super().__init__(bot)
        self.dossiers = dossiers

    async def cog_unload(self) -> None:
        await self.dossiers.aclose()

    def views(self) -> list[discord.ui.View]:
        """Persistent views: обе кнопки живут в тикете и должны пережить перезапуск."""
        return [AdmissionView(self.dossiers), ApprovalView(self.dossiers)]

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
        mechanic: discord.Role,
        builder: discord.Role,
        pilot: discord.Role,
        sniper: discord.Role,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            return
        mapping = dict(
            zip(
                ("BEDA", *SPECIALIZATIONS),
                (role.id for role in (beda, assault, medic, mechanic, builder, pilot, sniper)),
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
                        *zip(SPECIALIZATIONS, (assault, medic, mechanic, builder, pilot, sniper), strict=True),
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
