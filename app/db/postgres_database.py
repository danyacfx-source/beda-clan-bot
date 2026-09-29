"""PostgreSQL backend with the same small API as the SQLite Database wrapper.

The repositories deliberately depend on this narrow API instead of a concrete
driver. SQLite remains the default; PostgreSQL is enabled by using a
``postgresql://`` database URL.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]

_INSERT_PRIMARY_KEYS = {
    "warns": "id",
    "tickets": "ticket_id",
    "reminders": "id",
    "polls": "id",
    "giveaways": "id",
    "scheduled_messages": "id",
    "moderation_cases": "case_id",
    "events": "id",
}
_PARAM_RE = re.compile(r"\?")
_TABLE_RE = re.compile(r"\bINSERT\s+INTO\s+([a-z_]+)\b", re.IGNORECASE)


@dataclass(slots=True)
class PostgresCursor:
    rowcount: int = 0
    lastrowid: int | None = None


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT PRIMARY KEY,
    log_channel_id BIGINT,
    ticket_category_id BIGINT, member_log_channel_id BIGINT, message_log_channel_id BIGINT,
    voice_log_channel_id BIGINT, mod_log_channel_id BIGINT, bot_log_channel_id BIGINT,
    automod_enabled INTEGER NOT NULL DEFAULT 1,
    blocked_words TEXT NOT NULL DEFAULT '[]',
    ticket_panel_title TEXT NOT NULL DEFAULT 'Поддержка',
    ticket_panel_description TEXT NOT NULL DEFAULT 'Нажмите на кнопку, чтобы открыть тикет.',
    ticket_panel_footer TEXT NOT NULL DEFAULT 'Тикеты помогают решать личные вопросы без шума в каналах.',
    ticket_open_label TEXT NOT NULL DEFAULT 'Открыть тикет', ticket_open_emoji TEXT NOT NULL DEFAULT '🎫',
    ticket_intro_title TEXT NOT NULL DEFAULT 'Новый тикет',
    ticket_intro_description TEXT NOT NULL DEFAULT 'Опишите свою проблему, {member}.',
    ticket_intro_footer TEXT NOT NULL DEFAULT 'Нажмите кнопку ниже, чтобы закрыть тикет по завершении.',
    ticket_close_label TEXT NOT NULL DEFAULT 'Закрыть тикет', ticket_close_emoji TEXT NOT NULL DEFAULT '🔒',
    ticket_channel_prefix TEXT NOT NULL DEFAULT 'ticket'
);
CREATE TABLE IF NOT EXISTS warns (
    id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL UNIQUE,
    creator_id BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL,
    closed_at TEXT, transcript TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
    id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, guild_id BIGINT, channel_id BIGINT,
    message TEXT NOT NULL, remind_at TEXT NOT NULL, created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, processing_until TEXT
);
CREATE TABLE IF NOT EXISTS polls (
    id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
    message_id BIGINT, author_id BIGINT NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL,
    created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id BIGINT NOT NULL, user_id BIGINT NOT NULL, option INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id), FOREIGN KEY (poll_id) REFERENCES polls(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS giveaways (
    id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
    message_id BIGINT, author_id BIGINT NOT NULL, prize TEXT NOT NULL,
    winners INTEGER NOT NULL DEFAULT 1, ends_at TEXT NOT NULL, created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, processing_until TEXT, min_days INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
    PRIMARY KEY (giveaway_id, user_id), FOREIGN KEY (giveaway_id) REFERENCES giveaways(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS module_settings (
    guild_id BIGINT NOT NULL, module TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (guild_id, module)
);
CREATE TABLE IF NOT EXISTS temp_voices (
    owner_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS birthdays (
    user_id BIGINT PRIMARY KEY, month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    day INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31)
);
CREATE TABLE IF NOT EXISTS scheduled_messages (
    id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
    author_id BIGINT NOT NULL, content TEXT NOT NULL DEFAULT '', embed_json TEXT NOT NULL DEFAULT '{}',
    send_at TEXT NOT NULL, created_at TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
    processing_until TEXT
);
CREATE TABLE IF NOT EXISTS moderation_cases (
    case_id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, expires_at TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id BIGSERIAL PRIMARY KEY, actor_role TEXT NOT NULL, action TEXT NOT NULL,
    method TEXT NOT NULL, path TEXT NOT NULL, remote TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_hourly (
    guild_id BIGINT NOT NULL, bucket TEXT NOT NULL, messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, bucket)
);
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, message_id BIGINT,
    name TEXT NOT NULL, event_type TEXT NOT NULL DEFAULT 'freeform', description TEXT NOT NULL DEFAULT '',
    briefing_at TEXT NOT NULL, start_at TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '',
    show_not_going INTEGER NOT NULL DEFAULT 0, creator_id BIGINT NOT NULL, created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, reminded_briefing INTEGER NOT NULL DEFAULT 0,
    reminded_start INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS event_signup (
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL, role TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);
CREATE TABLE IF NOT EXISTS where_play (
    guild_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL, role_id BIGINT, message_id BIGINT,
    code TEXT NOT NULL DEFAULT '', team TEXT NOT NULL DEFAULT '', caller_id BIGINT,
    active INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS caller_rooms (
    guild_id BIGINT NOT NULL, member_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY (guild_id, member_id)
);
CREATE TABLE IF NOT EXISTS dossier_settings (
    guild_id BIGINT PRIMARY KEY, forum_id BIGINT NOT NULL, roles TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admissions (
    guild_id BIGINT NOT NULL, owner_id BIGINT NOT NULL, ticket_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, owner_id)
);
CREATE TABLE IF NOT EXISTS dossier_drafts (
    guild_id BIGINT NOT NULL, owner_id BIGINT NOT NULL, ticket_id BIGINT NOT NULL,
    data TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY (guild_id, owner_id)
);
CREATE TABLE IF NOT EXISTS dossiers (
    guild_id BIGINT NOT NULL, owner_id BIGINT NOT NULL, data TEXT NOT NULL,
    thread_id BIGINT, status TEXT NOT NULL, PRIMARY KEY (guild_id, owner_id)
);
CREATE INDEX IF NOT EXISTS idx_events_due ON events(active, start_at);
CREATE INDEX IF NOT EXISTS idx_events_guild ON events(guild_id, active);
CREATE INDEX IF NOT EXISTS idx_event_signup_event ON event_signup(event_id, role);
CREATE INDEX IF NOT EXISTS idx_where_play_active ON where_play(active);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(active, remind_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_messages(done, send_at);
CREATE INDEX IF NOT EXISTS idx_giveaways_due ON giveaways(active, ends_at);
CREATE INDEX IF NOT EXISTS idx_warns_guild_user ON warns(guild_id, user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_cases_guild_user ON moderation_cases(guild_id, user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_hourly_bucket ON activity_hourly(guild_id, bucket);
CREATE INDEX IF NOT EXISTS idx_dossier_drafts_ticket ON dossier_drafts(guild_id, ticket_id);
CREATE INDEX IF NOT EXISTS idx_dossiers_status ON dossiers(status);
INSERT INTO schema_migrations(version, applied_at)
SELECT version, CURRENT_TIMESTAMP::text FROM generate_series(1, 11) AS version
ON CONFLICT (version) DO NOTHING;
"""


def _translate_sql(sql: str) -> tuple[str, str | None]:
    """Переводит небольшой SQLite-диалект репозиториев в PostgreSQL."""
    normalized = sql.replace("datetime('now')", "CURRENT_TIMESTAMP::text")
    insert_ignore = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\b", normalized, re.IGNORECASE))
    normalized = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", normalized, flags=re.IGNORECASE)
    if insert_ignore and "ON CONFLICT" not in normalized.upper():
        normalized = normalized.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    if re.search(r"\bINSERT\s+OR\s+REPLACE\b", normalized, re.IGNORECASE):
        normalized = re.sub(r"\bINSERT\s+OR\s+REPLACE\b", "INSERT", normalized, flags=re.IGNORECASE)
        normalized = normalized.rstrip().rstrip(";") + (" ON CONFLICT (poll_id, user_id) DO UPDATE SET option = EXCLUDED.option")
    table_match = _TABLE_RE.search(normalized)
    table = table_match.group(1).lower() if table_match else None
    primary_key = _INSERT_PRIMARY_KEYS.get(table or "")
    has_returning = "RETURNING" in normalized.upper()
    if primary_key and not has_returning:
        normalized = normalized.rstrip().rstrip(";") + f" RETURNING {primary_key}"
    index = 0

    def replace(_match: re.Match[str]) -> str:
        nonlocal index
        index += 1
        return f"${index}"

    return _PARAM_RE.sub(replace, normalized), primary_key if primary_key and not has_returning else None


class PostgresDatabase:
    """Async PostgreSQL implementation used behind the Database facade."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.url,
            min_size=1,
            max_size=max(2, int(os.getenv("POSTGRES_POOL_SIZE", "8"))),
            command_timeout=30,
        )
        async with self.pool.acquire() as connection:
            await connection.execute(_POSTGRES_SCHEMA)

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL не подключён")
        return self.pool

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> PostgresCursor:
        pool = self._require_pool()
        query, generated_key = _translate_sql(sql)
        async with pool.acquire() as connection:
            if generated_key:
                rows = await connection.fetch(query, *params)
                lastrowid = int(rows[0][generated_key]) if rows else None
                return PostgresCursor(rowcount=len(rows), lastrowid=lastrowid)
            status = await connection.execute(query, *params)
        return PostgresCursor(rowcount=_command_rowcount(status))

    async def execute_returning(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        pool = self._require_pool()
        query, _ = _translate_sql(sql)
        async with pool.acquire() as connection:
            row = await connection.fetchrow(query, *params)
        return dict(row) if row is not None else None

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        pool = self._require_pool()
        query, _ = _translate_sql(sql)
        async with pool.acquire() as connection:
            row = await connection.fetchrow(query, *params)
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        pool = self._require_pool()
        query, _ = _translate_sql(sql)
        async with pool.acquire() as connection:
            rows = await connection.fetch(query, *params)
        return [dict(row) for row in rows]

    async def integrity_check(self) -> str:
        row = await self.fetchone("SELECT 1 AS result")
        return "ok" if row and row["result"] == 1 else "error"

    async def backup(self, destination: str | Path) -> Path:
        """Делает PostgreSQL backup через pg_dump в custom format."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.tmp")
        process = await asyncio.create_subprocess_exec(
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--file",
            str(temp),
            self.url,
        )
        return_code = await process.wait()
        if return_code != 0:
            temp.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump завершился с кодом {return_code}")
        os.replace(temp, target)
        return target

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


def _command_rowcount(status: str) -> int:
    match = re.search(r"\b(\d+)$", status)
    return int(match.group(1)) if match else 0
