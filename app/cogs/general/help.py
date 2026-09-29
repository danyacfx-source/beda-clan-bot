"""Интерактивный каталог команд бота «BEDA»."""

from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import app_commands

from app.core import embeds
from app.core.base import ClanCog
from app.utils.format import truncate


@dataclass(frozen=True, slots=True)
class HelpCategory:
    key: str
    label: str
    emoji: str
    description: str
    commands: frozenset[str]


_CATEGORIES = (
    HelpCategory(
        "general",
        "Основное",
        "⌁",
        "Информация, профиль и полезные команды.",
        frozenset({"help", "ping", "health", "about", "serverinfo", "userinfo"}),
    ),
    HelpCategory(
        "moderation",
        "Модерация",
        "🛡️",
        "Порядок и управление участниками.",
        frozenset(
            {
                "moderation",
                "warn",
                "warns",
                "clearwarns",
                "cases",
                "case",
                "automod",
                "kick",
                "ban",
                "unban",
                "timeout",
                "purge",
                "slowmode",
            }
        ),
    ),
    HelpCategory(
        "community",
        "Сообщество",
        "✦",
        "Опросы, розыгрыши, роли и события.",
        frozenset(
            {
                "poll",
                "giveaway",
                "roles",
                "birthday",
                "event",
                "event_edit",
                "event_cancel",
                "event_list",
                "event_signup",
                "where_play",
                "where_play_status",
                "where_play_card",
                "stop_play",
            }
        ),
    ),
    HelpCategory(
        "support",
        "Поддержка",
        "◈",
        "Тикеты, личные дела, настройки и панели управления.",
        frozenset(
            {
                "ticket",
                "setup_dossiers",
                "review_dossier",
                "approve_interview",
                "setup",
                "permissions",
                "embed",
                "scheduler",
                "schedule",
            }
        ),
    ),
    HelpCategory(
        "tools",
        "Инструменты",
        "◇",
        "Напоминания, временные каналы и утилиты.",
        frozenset(
            {
                "remind",
                "remindme",
                "tempvoice",
                "snipe",
                "editsnipe",
                "avatar",
                "servericon",
                "emoji",
                "roleinfo",
                "channelinfo",
                "whois",
                "lock",
                "unlock",
            }
        ),
    ),
)


def _flatten(bot: discord.Client) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for command in bot.tree.get_commands():
        children = getattr(command, "commands", None)
        if children:
            for child in children:
                result.append((command.name, f"/{command.name} {child.name}", child.description))
        else:
            result.append((command.name, f"/{command.name}", command.description))
    return sorted(result, key=lambda item: item[1])


def _category_for(root_name: str) -> str:
    for category in _CATEGORIES:
        if root_name in category.commands:
            return category.key
    return "tools"


def _overview(bot: discord.Client, entries: list[tuple[str, str, str]]) -> discord.Embed:
    embed = embeds.brand(
        "Центр управления",
        "Все возможности бота собраны по разделам. Выберите категорию в меню ниже.",
        footer=f"BEDA  •  {len(entries)} команд  •  выберите раздел",
    )
    if bot.user:
        embed.set_author(name=bot.user.display_name, icon_url=bot.user.display_avatar.url)
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    for category in _CATEGORIES:
        count = sum(_category_for(root) == category.key for root, _, _ in entries)
        embed.add_field(
            name=f"{category.emoji}  {category.label}",
            value=f"{category.description}\n`{count} команд`",
            inline=True,
        )
    embed.add_field(name="Быстрый старт", value="`/ping`  `/about`  `/serverinfo`  `/help`", inline=False)
    return embed


def _category_embed(category: HelpCategory, entries: list[tuple[str, str, str]]) -> discord.Embed:
    selected = [(name, description) for root, name, description in entries if _category_for(root) == category.key]
    lines = [f"**{name}**\n{truncate(description or 'Описание не указано', 90)}" for name, description in selected]
    return embeds.brand(
        f"{category.emoji}  {category.label}",
        "\n\n".join(lines) or "В этом разделе пока нет команд.",
        footer=f"BEDA  •  {len(selected)} команд  •  /help",
    )


class HelpView(discord.ui.View):
    def __init__(self, bot: discord.Client, user_id: int, entries: list[tuple[str, str, str]]) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.entries = entries
        self.message: discord.InteractionMessage | None = None
        options = [
            discord.SelectOption(label="Обзор", value="overview", emoji="⌁", description="Все разделы и быстрый старт"),
            *[
                discord.SelectOption(
                    label=category.label,
                    value=category.key,
                    emoji=category.emoji,
                    description=category.description[:100],
                )
                for category in _CATEGORIES
            ],
        ]
        select = discord.ui.Select(placeholder="Выберите раздел", options=options)
        select.callback = self._select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            embed=embeds.warning("Это меню открыто не вами", "Используйте `/help`, чтобы открыть своё."),
            ephemeral=True,
        )
        return False

    async def _select(self, interaction: discord.Interaction) -> None:
        select = self.children[0]
        if not isinstance(select, discord.ui.Select) or not select.values:
            await interaction.response.send_message(
                embed=embeds.error("Ошибка", "Меню справки больше недоступно."),
                ephemeral=True,
            )
            return
        key = select.values[0]
        embed = _overview(self.bot, self.entries)
        if key != "overview":
            category = next(item for item in _CATEGORIES if item.key == key)
            embed = _category_embed(category, self.entries)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class HelpCog(ClanCog, name="Справка"):
    @app_commands.command(name="help", description="Открыть каталог команд бота")
    async def help_command(self, interaction: discord.Interaction) -> None:
        entries = _flatten(self.bot)
        view = HelpView(self.bot, interaction.user.id, entries)
        await interaction.response.send_message(embed=_overview(self.bot, entries), view=view, ephemeral=True)
        view.message = await interaction.original_response()
