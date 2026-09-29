"""Сервис тикетов: создание, закрытие, транскрипты."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord

from app.core import embeds
from app.core.ticket_content import (
    CLAN_INTRO_FIELDS,
    CLAN_INTRO_FOOTER,
    CLAN_INTRO_INLINE,
    CLAN_INTRO_TEXT,
    CLAN_INTRO_TITLE,
)
from app.core.views import TicketCloseView
from app.types import TicketRow

if TYPE_CHECKING:
    from app.config import Config
    from app.db.tickets_repository import TicketsRepository
    from app.services.logging_service import LoggingService
    from app.services.settings_service import SettingsService

_MAX_TRANSCRIPT_MESSAGES = 300
logger = logging.getLogger("bot.tickets")


@dataclass(slots=True)
class TicketCreateResult:
    channel: discord.TextChannel | None
    error: str | None = None


@dataclass(slots=True)
class TicketCloseResult:
    error: str | None = None
    transcript_channel_mention: str | None = None


class TicketService:
    def __init__(
        self,
        settings: SettingsService,
        tickets_repo: TicketsRepository,
        logging_service: LoggingService,
        config: Config | None = None,
    ) -> None:
        self._settings = settings
        self._repo = tickets_repo
        self._logging = logging_service
        self._config = config
        self._delete_tasks: set[asyncio.Task[None]] = set()

    async def create(self, guild: discord.Guild, member: discord.Member) -> TicketCreateResult:
        if await self._repo.has_open_by_creator(guild.id, member.id):
            return TicketCreateResult(channel=None, error="У вас уже есть открытый тикет.")

        settings = await self._settings.get(guild.id)
        category_id = settings.get("ticket_category_id") or (self._config.ticket_category_id if self._config else None)
        category = guild.get_channel(category_id) if category_id else None
        bot_member = guild.me
        if bot_member is None:
            return TicketCreateResult(channel=None, error="Бот ещё не готов на этом сервере.")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        }
        for role_id in self._config.ticket_support_role_ids if self._config else ():
            role = guild.get_role(role_id)
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    manage_messages=True,
                )
        name = re.sub(r"[^a-z0-9-]", "-", member.name.lower()).strip("-") or "ticket"
        prefix = re.sub(r"[^a-z0-9-]", "-", (settings.get("ticket_channel_prefix") or "ticket").lower()).strip("-") or "ticket"
        try:
            channel = await guild.create_text_channel(
                name=f"{prefix}-{name}",
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                reason=f"Тикет от {member}",
            )
        except discord.HTTPException as exc:
            return TicketCreateResult(channel=None, error=f"Discord API: {exc}")

        ticket_id: int | None = None
        try:
            now = datetime.now(UTC)
            ticket_id = await self._repo.create(guild.id, channel.id, member.id, now)

            intro_title = settings.get("ticket_intro_title") or CLAN_INTRO_TITLE
            intro_text = (settings.get("ticket_intro_description") or CLAN_INTRO_TEXT).replace(
                "{member}", member.mention
            )
            intro = embeds.brand(
                intro_title,
                intro_text,
                settings.get("ticket_intro_footer") or CLAN_INTRO_FOOTER,
            )
            for index, (name, value) in enumerate(CLAN_INTRO_FIELDS):
                intro.add_field(name=name, value=value, inline=index < CLAN_INTRO_INLINE)
            await channel.send(
                embed=intro,
                view=TicketCloseView(
                    self,
                    label=settings.get("ticket_close_label") or "Закрыть тикет",
                    emoji=settings.get("ticket_close_emoji"),
                ),
            )
        except discord.HTTPException as exc:
            await self._rollback_failed_create(channel, ticket_id)
            return TicketCreateResult(channel=None, error=f"Discord API: {exc}")
        except Exception:
            await self._rollback_failed_create(channel, ticket_id)
            logger.exception("Не удалось завершить создание тикета в канале %s", channel.id)
            return TicketCreateResult(channel=None, error="Внутренняя ошибка при создании тикета.")
        return TicketCreateResult(channel=channel)

    async def get_open_ticket(self, guild_id: int, channel_id: int) -> TicketRow | None:
        ticket = await self._repo.by_channel(channel_id)
        if ticket and ticket["status"] == "open":
            return ticket
        return None

    async def list_tickets(self, guild_id: int, limit: int = 100) -> list[TicketRow]:
        return await self._repo.list_for_guild(guild_id, limit)

    async def get_ticket(self, ticket_id: int) -> TicketRow | None:
        return await self._repo.get(ticket_id)

    async def close_by_id(self, guild: discord.Guild, ticket_id: int, closer: discord.Member | discord.ClientUser) -> TicketCloseResult:
        """Закрывает тикет по номеру (без привязки к каналу) — для веб-панели."""
        ticket = await self._repo.get(ticket_id)
        if ticket is None or ticket.get("guild_id") != guild.id:
            return TicketCloseResult(error="Тикет не найден.")
        if ticket["status"] != "open":
            return TicketCloseResult(error="Тикет уже закрыт.")
        channel = guild.get_channel(ticket["channel_id"])
        if channel is None:
            await self._repo.close(ticket["ticket_id"], datetime.now(UTC))
            await self._repo.save_transcript(ticket["ticket_id"], "")
            return TicketCloseResult(transcript_channel_mention="(канал тикета уже удалён)")
        return await self.close(guild, channel, closer)

    async def close(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel | None,
        closer: discord.Member,
    ) -> TicketCloseResult:
        if channel is None:
            return TicketCloseResult(error="Не удалось определить канал тикета.")

        ticket = await self._repo.by_channel(channel.id)
        if ticket is None:
            return TicketCloseResult(error="Этот канал не является тикетом.")
        if ticket["status"] != "open":
            return TicketCloseResult(error="Тикет уже закрыт.")

        await self._repo.close(ticket["ticket_id"], datetime.now(UTC))

        transcript = await self._build_transcript(guild, channel)
        summary = embeds.info(
            "Тикет закрыт",
            f"Закрыл: {closer.mention}\nСистемный номер: `{ticket['ticket_id']}`",
        )
        if transcript is not None:
            await self._repo.save_transcript(ticket["ticket_id"], transcript.text)
            await self._send_transcript(guild, channel, transcript.file, summary)
            transcript.cleanup()

        task = asyncio.create_task(self._delete_after(channel))
        self._delete_tasks.add(task)
        task.add_done_callback(self._delete_tasks.discard)

        target = await self._logging.target_channel(guild)
        mention = target.mention if target else channel.mention
        return TicketCloseResult(transcript_channel_mention=mention)

    async def aclose(self) -> None:
        """Отменяет отложенное удаление каналов при выгрузке cog."""
        tasks = tuple(task for task in self._delete_tasks if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._delete_tasks.clear()

    async def _rollback_failed_create(self, channel: discord.TextChannel, ticket_id: int | None) -> None:
        if ticket_id is not None:
            try:
                await self._repo.close(ticket_id, datetime.now(UTC))
            except Exception:
                logger.exception("Не удалось закрыть незавершённый тикет %s", ticket_id)
        try:
            await channel.delete(reason="Откат неудачного создания тикета")
        except discord.HTTPException:
            logger.debug("Не удалось удалить канал незавершённого тикета %s", channel.id, exc_info=True)

    async def _build_transcript(self, guild: discord.Guild, channel: discord.TextChannel):
        lines: list[str] = []
        try:
            async for message in channel.history(limit=_MAX_TRANSCRIPT_MESSAGES, oldest_first=True):
                content = message.clean_content.strip() or "(без текста)"
                lines.append(f"[{message.created_at:%Y-%m-%d %H:%M}] {message.author} ({message.author.id}): {content}")
        except discord.HTTPException:
            lines.append("(не удалось получить историю сообщений)")

        data = "\n".join(lines or ["(тикет без сообщений)"])

        class _Transcript:
            file: discord.File
            text: str

            def __init__(self, file: discord.File, text: str) -> None:
                self.file = file
                self.text = text

            def cleanup(self) -> None:
                self.file.fp.close()

        buffer = io.BytesIO(data.encode("utf-8"))
        return _Transcript(discord.File(buffer, filename=f"transcript-{channel.name}.txt"), data)

    async def _send_transcript(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        file: discord.File,
        summary: discord.Embed,
    ) -> None:
        target = await self._logging.target_channel(guild) or channel
        try:
            await target.send(embed=summary, file=file)
        except discord.HTTPException:
            try:
                file.fp.seek(0)
            except (AttributeError, ValueError):
                logger.debug("Не удалось перемотать файл транскрипта", exc_info=True)
            try:
                await channel.send(embed=summary, file=file)
            except discord.HTTPException:
                pass

    @staticmethod
    async def _delete_after(channel: discord.TextChannel, delay: float = 10.0) -> None:
        await asyncio.sleep(delay)
        try:
            await channel.delete(reason="Тикет закрыт")
        except discord.HTTPException:
            pass
