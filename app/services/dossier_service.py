"""Личные дела: допуск после собеседования, анкета, проверка и публикация.

Схема работы: открытый тикет → ``/approve_interview`` администратора →
заявитель заполняет анкету кнопкой в этом же тикете → ``/review_dossier``
администратора → тред на форуме и выдача ролей.

Отличия от исходной реализации в ``beda-discord-bot``:

* город и Steam ID запрашиваются в самой анкете: тикеты этого проекта не
  хранят данные заявки, поэтому брать их неоткуда;
* ``CooldownGuard`` заменён скользящим окном в памяти — единственная кнопка
  формы не warrants отдельную таблицу;
* аудит ведётся через ``logger`` вместо отдельного журнала.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Final

import discord

from app.core.base import BaseService
from app.db.dossiers_repository import DossiersRepository
from app.utils.text import clip, normalize_space, safe_component

__all__ = [
    "SPECIALIZATIONS",
    "DossierPermissionError",
    "DossierValidationError",
    "DossierService",
    "dossier_card",
    "specialization_asset",
    "validate_dossier",
]

logger = logging.getLogger("bot.dossiers")

PROJECT_ROOT: Final = Path(__file__).resolve().parent.parent.parent
ASSET_DIR: Final = (PROJECT_ROOT / "assets" / "specializations").resolve()

SPECIALIZATIONS: Final = ("Штурмовик", "Медик", "Механик", "Строитель", "Пилот", "Снайпер")

FIELDS: Final = (
    ("nickname", "Ник", 60),
    ("name", "Имя", 60),
    ("city", "Город", 60),
    ("steam_id", "Steam ID", 100),
)

CARD_FIELDS: Final = (
    ("nickname", "Ник"),
    ("name", "Имя"),
    ("steam_id", "Steam ID"),
    ("city", "Город"),
    ("primary", "Основная специализация"),
    ("secondary", "Дополнительная специализация"),
)


class DossierValidationError(ValueError):
    """Ожидаемая ошибка заполнения с текстом, безопасным для показа пользователю."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class DossierPermissionError(PermissionError):
    """Не хватает прав бота или администратора; текст предназначен пользователю."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def validate_dossier(data: dict[str, Any], *, require_source: bool = True) -> dict[str, Any]:
    """Проверить и нормализовать анкету.

    ``require_source=False`` разрешает пустые город и Steam ID — такой режим
    нужен при предварительной проверке, когда их уточняет администратор.
    """
    cleaned = dict(data)
    for key, label, maximum in FIELDS:
        cleaned[key] = normalize_space(cleaned.get(key, ""))[: maximum + 1]
        if not require_source and key in {"city", "steam_id"} and not cleaned[key]:
            continue
        if not 1 <= len(cleaned[key]) <= maximum:
            raise DossierValidationError(f"{label}: требуется от 1 до {maximum} символов.")
    cleaned.pop("timezone", None)
    if cleaned.get("primary") not in SPECIALIZATIONS or cleaned.get("secondary") not in SPECIALIZATIONS:
        raise DossierValidationError("Выберите обе специализации.")
    if cleaned["primary"] == cleaned["secondary"]:
        raise DossierValidationError("Основная и дополнительная специализации должны различаться.")
    return cleaned


def specialization_asset(specialization: str) -> Path:
    """Найти картинку специализации, не выпуская её за пределы папки ассетов.

    ``SPECIALIZATIONS`` — первый рубеж, проверка принадлежности каталогу —
    второй: код, который забудет про whitelist, всё равно не прочитает
    произвольный файл на хосте.
    """
    if specialization not in SPECIALIZATIONS:
        raise DossierValidationError("Неизвестная специализация.")
    candidate = (ASSET_DIR / f"{safe_component(specialization)}.png").resolve()
    if candidate.parent != ASSET_DIR:
        raise DossierValidationError("Недопустимое имя файла.")
    if not candidate.is_file():
        raise DossierValidationError(f"Отсутствует изображение специализации: {candidate.name}")
    return candidate


def _add_card_fields(embed: discord.Embed, data: dict[str, Any]) -> None:
    for key, label in CARD_FIELDS:
        value = data.get(key) or "Не заполнено"
        embed.add_field(
            name=label,
            value=discord.utils.escape_markdown(clip(str(value), 1024)),
            inline=False,
        )


def dossier_card(data: dict[str, Any], owner: int) -> tuple[discord.Embed, str]:
    """Собрать карточку личного дела для публикации. Возвращает embed и основную специализацию."""
    validated = validate_dossier(dict(data))
    embed = discord.Embed(
        title="BEDA / ЛИЧНОЕ ДЕЛО",
        description=f"Участник: <@{owner}>",
        color=0xB89960,
    )
    _add_card_fields(embed, validated)
    embed.set_image(url="attachment://specialization.png")
    return embed, validated["primary"]


def review_card(data: dict[str, Any], owner: int) -> discord.Embed:
    """Карточка предпросмотра для администратора: показывает сырые данные анкеты."""
    embed = discord.Embed(
        title="Проверка личного дела",
        description=f"Заявитель: <@{owner}>",
        color=discord.Color.blue(),
    )
    _add_card_fields(embed, data)
    return embed


class DossierService(BaseService["DossiersRepository"]):
    """Бизнес-логика личных дел поверх репозитория."""

    def __init__(self, repo: DossiersRepository) -> None:
        super().__init__(repo)
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    async def aclose(self) -> None:
        self._windows.clear()

    def _throttled(self, key: str, *, limit: int, window: float) -> float:
        """Зарегистрировать действие. Возвращает 0, если можно, иначе секунды до отпуска."""
        now = time.monotonic()
        hits = self._windows[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            return max(0.0, window - (now - hits[0]))
        hits.append(now)
        return 0.0

    def resolve_roles(self, guild: discord.Guild, mapping: dict[str, int]) -> list[discord.Role]:
        """Проверить, что роли настроены и бот может их выдать."""
        roles: list[discord.Role] = []
        for name in ("BEDA", *SPECIALIZATIONS):
            role = guild.get_role(mapping.get(name, 0)) if mapping.get(name) else None
            if role is None or role.is_default() or role.managed or role >= guild.me.top_role:
                raise DossierPermissionError(
                    "Боту нужны «Управлять ролями» и роль выше BEDA и всех специализаций. "
                    "Проверьте /setup_dossiers."
                )
            roles.append(role)
        if not guild.me.guild_permissions.manage_roles:
            raise DossierPermissionError("Боту нужно право «Управлять ролями».")
        return roles

    async def setup(self, guild: discord.Guild, forum: discord.ForumChannel, mapping: dict[str, int]) -> None:
        """Проверить форум и роли, затем сохранить настройки."""
        if len(set(mapping.values())) != len(mapping):
            raise DossierValidationError("Для BEDA и каждой специализации выберите отдельную роль.")
        if forum.flags.require_tag:
            raise DossierValidationError("Отключите обязательный тег в настройках форума.")
        permissions = forum.permissions_for(guild.me)
        missing = [
            name
            for name in ("view_channel", "send_messages", "send_messages_in_threads", "attach_files")
            if not getattr(permissions, name, False)
        ]
        if missing:
            raise DossierValidationError(
                "Боту нужен доступ к форуму, создание публикаций, отправка сообщений в ветках и вложений."
            )
        self.resolve_roles(guild, mapping)
        await self._repo.save_settings(guild.id, forum.id, json.dumps(mapping, ensure_ascii=False))

    async def settings(self, guild_id: int) -> dict[str, int] | None:
        """Настроенные роли по guild_id либо ``None``, если ``/setup_dossiers`` не выполнялся."""
        row = await self._repo.settings(guild_id)
        if row is None:
            return None
        try:
            return {str(key): int(value) for key, value in json.loads(row["roles"]).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.error("Не читается настройка ролей для guild %s", guild_id)
            return None

    async def form_block_reason(self, guild_id: int, user_id: int, ticket_id: int) -> str:
        """Почему кнопка анкеты сейчас недоступна. Пустая строка — можно открывать форму."""
        if not await self._repo.admitted(guild_id, user_id, ticket_id):
            return "Анкета доступна только одобренному заявителю в его тикете."
        wait = self._throttled(f"form:{guild_id}:{user_id}", limit=5, window=300.0)
        if wait:
            return f"Слишком много запросов. Повторите через {max(1, round(wait))} с."
        return ""

    async def ticket_owner(self, guild_id: int, ticket_id: int) -> int | None:
        """Владелец открытого тикета либо ``None``, если тикета нет или он закрыт."""
        return await self._repo.ticket_owner(guild_id, ticket_id)

    async def approve(self, guild_id: int, owner_id: int, ticket_id: int) -> None:
        """Отметить собеседование пройденным."""
        if await self._repo.settings(guild_id) is None:
            raise DossierValidationError("Сначала выполните /setup_dossiers.")
        await self._repo.admit(guild_id, owner_id, ticket_id)

    async def submit_draft(self, guild_id: int, owner_id: int, ticket_id: int, data: dict[str, Any]) -> None:
        """Сохранить черновик анкеты, от bumping revision."""
        if not await self._repo.admitted(guild_id, owner_id, ticket_id):
            raise DossierValidationError(
                "Анкета доступна только одобренному заявителю в его открытом тикете."
            )
        if await self._repo.dossier(guild_id, owner_id) is not None:
            raise DossierValidationError(
                "Личное дело уже создано или находится в процессе публикации. Обратитесь к администратору."
            )
        await self._repo.save_draft(guild_id, owner_id, ticket_id, json.dumps(data, ensure_ascii=False))

    async def prepare_review(
        self, guild_id: int, ticket_id: int, city: str | None, steam_id: str | None
    ) -> tuple[int, dict[str, Any], int, list[str]]:
        """Собрать данные для предпросмотра.

        Возвращает ``(owner, data, revision, missing)``: ``missing`` — поля,
        которые администратор должен уточнить командой ``/review_dossier``.
        Публикуется только когда ``missing`` пуст.
        """
        draft = await self._repo.draft_for_ticket(guild_id, ticket_id)
        if draft is None:
            raise DossierValidationError("В этом открытом тикете ещё нет заполненной анкеты.")
        try:
            data = json.loads(draft["data"])
        except json.JSONDecodeError as error:
            raise DossierValidationError("Сохранённая анкета повреждена.") from error

        owner = int(draft["owner_id"])
        revision = int(draft["revision"])
        published = await self._repo.dossier(guild_id, owner)

        if published is not None:
            if city is not None or steam_id is not None:
                raise DossierValidationError("Публикация уже началась: её данные менять нельзя.")
            try:
                data = json.loads(published["data"])
            except json.JSONDecodeError as error:
                raise DossierValidationError("Опубликованное дело повреждено.") from error
            return owner, data, revision, []

        corrected = False
        for key, value in (("city", city), ("steam_id", steam_id)):
            if value is not None:
                data[key] = value
                corrected = True
        data = validate_dossier(data, require_source=False)
        if corrected:
            revision += 1
            await self._repo.update_draft(guild_id, owner, json.dumps(data, ensure_ascii=False), revision)

        missing = [key for key in ("city", "steam_id") if not data.get(key)]
        return owner, data, revision, missing

    async def publish(self, guild: discord.Guild, owner_id: int, ticket_id: int, revision: int) -> str:
        """Опубликовать личное дело и выдать роли.

        Возвращает ``"ready"``, ``"abandoned"`` либо ``"roles_pending"``.
        Повторные вызовы безопасны: уже опубликованное дело не дублируется.
        """
        if not await self._repo.admitted(guild.id, owner_id, ticket_id):
            raise DossierValidationError("Собеседование ещё не одобрено.")

        draft = await self._repo.draft(guild.id, owner_id)
        if draft is None or int(draft["revision"]) != revision or int(draft["ticket_id"]) != ticket_id:
            raise DossierValidationError("Анкета изменилась. Откройте /review_dossier заново.")
        try:
            data = json.loads(draft["data"])
        except json.JSONDecodeError as error:
            raise DossierValidationError("Сохранённая анкета повреждена.") from error

        existing = await self._repo.dossier(guild.id, owner_id)
        if existing is not None and existing["status"] == "ready":
            raise DossierValidationError(f"Личное дело уже существует: <#{existing['thread_id']}>")
        if existing is not None and existing["thread_id"] is None:
            raise DossierValidationError(
                "Предыдущая публикация не завершена. Администратору нужно сверить форум и запись dossiers "
                "в базе перед повтором, чтобы исключить дубль."
            )

        settings = await self._repo.settings(guild.id)
        if settings is None:
            raise DossierValidationError("Сначала выполните /setup_dossiers.")

        if existing is not None:
            data = json.loads(existing["data"])
        else:
            try:
                data = validate_dossier(data)
            except DossierValidationError as error:
                raise DossierValidationError(f"{error.user_message} Проверьте /review_dossier.") from error

        roles = self.resolve_roles(guild, json.loads(settings["roles"]))

        if existing is not None:
            thread_id = int(existing["thread_id"])
        else:
            thread_id = await self._create_thread(guild, owner_id, data, int(settings["forum_id"]))

        member = guild.get_member(owner_id) or await self._fetch_member(guild, owner_id)
        if member is None:
            await self._repo.set_status(guild.id, owner_id, "abandoned")
            logger.info(
                "dossier_abandoned actor=? guild=%s owner=%s",
                guild.id,
                owner_id,
            )
            return "abandoned"

        try:
            await member.add_roles(*roles, reason="Успешное собеседование и создание личного дела BEDA")
        except discord.HTTPException:
            logger.warning(
                "Не удалось выдать роли guild=%s owner=%s thread=%s",
                guild.id,
                owner_id,
                thread_id,
            )
            return "roles_pending"

        await self._repo.set_status(guild.id, owner_id, "ready")
        logger.info("dossier_published guild=%s owner=%s thread=%s", guild.id, owner_id, thread_id)
        return "ready"

    async def _create_thread(self, guild: discord.Guild, owner_id: int, data: dict[str, Any], forum_id: int) -> int:
        forum = await self._forum(guild, forum_id)
        embed, primary = dossier_card(data, owner_id)
        image_path = specialization_asset(primary)
        if image_path.stat().st_size > guild.filesize_limit:
            raise DossierValidationError("Изображение превышает лимит вложений сервера.")

        await self._repo.reserve(guild.id, owner_id, json.dumps(data, ensure_ascii=False))
        attachment = discord.File(image_path, filename="specialization.png")
        try:
            thread = await forum.create_thread(
                name=clip(f"Личное дело — {data['nickname']}", 100),
                embed=embed,
                file=attachment,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            await self._repo.drop(guild.id, owner_id)
            raise
        finally:
            attachment.close()

        thread_id = int(thread.thread.id)
        await self._repo.set_thread(guild.id, owner_id, thread_id)
        return thread_id

    async def _forum(self, guild: discord.Guild, forum_id: int) -> discord.ForumChannel:
        try:
            channel = await guild.fetch_channel(forum_id)
        except discord.NotFound as error:
            raise DossierValidationError("Форум личных дел не найден. Проверьте /setup_dossiers.") from error
        if not isinstance(channel, discord.ForumChannel):
            raise DossierValidationError("В настройках указан канал, а не форум. Проверьте /setup_dossiers.")
        return channel

    @staticmethod
    async def _fetch_member(guild: discord.Guild, owner_id: int) -> discord.Member | None:
        try:
            return await guild.fetch_member(owner_id)
        except discord.NotFound:
            return None
