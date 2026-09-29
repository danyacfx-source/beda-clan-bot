"""Репозиторий личных дел: настройки, допуски, черновики и публикации."""

from __future__ import annotations

from typing import cast

from app.db.base_repository import BaseRepository
from app.types import (
    DossierDraftRow,
    DossierDraftTicketRow,
    DossierRow,
    DossierSettingsRow,
)


class DossiersRepository(BaseRepository):
    """Доступ к таблицам ``dossier_settings``, ``admissions``, ``dossiers``, ``dossier_drafts``.

    Допуск и черновик всегда сверяются с ``tickets``: анкета имеет силу только
    внутри открытого тикета, поэтому закрытие тикета автоматически лишает
    заявителя права заполнить дело.
    """

    async def settings(self, guild_id: int) -> DossierSettingsRow | None:
        row = await self.db.fetchone(
            "SELECT * FROM dossier_settings WHERE guild_id = ?",
            (guild_id,),
        )
        return cast(DossierSettingsRow, dict(row)) if row else None

    async def save_settings(self, guild_id: int, forum_id: int, roles: str) -> None:
        await self.db.execute(
            """
            INSERT INTO dossier_settings (guild_id, forum_id, roles) VALUES (?, ?, ?)
            ON CONFLICT (guild_id) DO UPDATE SET forum_id = EXCLUDED.forum_id, roles = EXCLUDED.roles
            """,
            (guild_id, forum_id, roles),
        )

    async def ticket_owner(self, guild_id: int, ticket_id: int) -> int | None:
        row = await self.db.fetchone(
            "SELECT creator_id FROM tickets WHERE guild_id = ? AND channel_id = ? AND status = 'open'",
            (guild_id, ticket_id),
        )
        return int(row["creator_id"]) if row else None

    async def admitted(self, guild_id: int, owner_id: int, ticket_id: int) -> bool:
        row = await self.db.fetchone(
            """
            SELECT 1 FROM admissions a
              JOIN tickets t ON t.channel_id = a.ticket_id
                          AND t.guild_id = a.guild_id
                          AND t.creator_id = a.owner_id
            WHERE a.guild_id = ? AND a.owner_id = ? AND a.ticket_id = ? AND t.status = 'open'
            LIMIT 1
            """,
            (guild_id, owner_id, ticket_id),
        )
        return row is not None

    async def admit(self, guild_id: int, owner_id: int, ticket_id: int) -> None:
        await self.db.execute(
            """
            INSERT INTO admissions (guild_id, owner_id, ticket_id) VALUES (?, ?, ?)
            ON CONFLICT (guild_id, owner_id) DO UPDATE SET ticket_id = EXCLUDED.ticket_id
            """,
            (guild_id, owner_id, ticket_id),
        )

    async def save_draft(self, guild_id: int, owner_id: int, ticket_id: int, data: str) -> None:
        await self.db.execute(
            """
            INSERT INTO dossier_drafts (guild_id, owner_id, ticket_id, data, revision) VALUES (?, ?, ?, ?, 1)
            ON CONFLICT (guild_id, owner_id) DO UPDATE SET
                ticket_id = EXCLUDED.ticket_id,
                data = EXCLUDED.data,
                revision = dossier_drafts.revision + 1
            """,
            (guild_id, owner_id, ticket_id, data),
        )

    async def update_draft(self, guild_id: int, owner_id: int, data: str, revision: int) -> None:
        await self.db.execute(
            "UPDATE dossier_drafts SET data = ?, revision = ? WHERE guild_id = ? AND owner_id = ?",
            (data, revision, guild_id, owner_id),
        )

    async def draft(self, guild_id: int, owner_id: int) -> DossierDraftRow | None:
        row = await self.db.fetchone(
            "SELECT * FROM dossier_drafts WHERE guild_id = ? AND owner_id = ?",
            (guild_id, owner_id),
        )
        return cast(DossierDraftRow, dict(row)) if row else None

    async def draft_for_ticket(self, guild_id: int, ticket_id: int) -> DossierDraftTicketRow | None:
        row = await self.db.fetchone(
            """
            SELECT d.owner_id, d.data, d.revision FROM dossier_drafts d
              JOIN tickets t ON t.channel_id = d.ticket_id
                          AND t.guild_id = d.guild_id
                          AND t.creator_id = d.owner_id
            WHERE d.guild_id = ? AND d.ticket_id = ? AND t.status = 'open'
            """,
            (guild_id, ticket_id),
        )
        return cast(DossierDraftTicketRow, dict(row)) if row else None

    async def dossier(self, guild_id: int, owner_id: int) -> DossierRow | None:
        row = await self.db.fetchone(
            "SELECT * FROM dossiers WHERE guild_id = ? AND owner_id = ?",
            (guild_id, owner_id),
        )
        return cast(DossierRow, dict(row)) if row else None

    async def reserve(self, guild_id: int, owner_id: int, data: str) -> None:
        """Зарезервировать место под публикацию до создания треда.

        Запись появляется раньше треда: если создание треда упадёт, её удалят, а
        повторная попытка не сможёт прочитать запись без ``thread_id``.
        """
        await self.db.execute(
            "INSERT INTO dossiers (guild_id, owner_id, data, thread_id, status) VALUES (?, ?, ?, NULL, 'publishing')",
            (guild_id, owner_id, data),
        )

    async def drop(self, guild_id: int, owner_id: int) -> None:
        await self.db.execute(
            "DELETE FROM dossiers WHERE guild_id = ? AND owner_id = ?",
            (guild_id, owner_id),
        )

    async def set_thread(self, guild_id: int, owner_id: int, thread_id: int) -> None:
        await self.db.execute(
            "UPDATE dossiers SET thread_id = ?, status = 'roles_pending' WHERE guild_id = ? AND owner_id = ?",
            (thread_id, guild_id, owner_id),
        )

    async def set_status(self, guild_id: int, owner_id: int, status: str) -> None:
        await self.db.execute(
            "UPDATE dossiers SET status = ? WHERE guild_id = ? AND owner_id = ?",
            (status, guild_id, owner_id),
        )
