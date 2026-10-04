#!/usr/bin/env python3
"""Переносит данные из SQLite в пустую PostgreSQL-базу.

Сначала создайте новую PostgreSQL-базу, затем остановите бота и запустите:

    python scripts/migrate_sqlite_to_postgres.py \
        --source data/bot.db \
        --target "$DATABASE_URL"

По умолчанию скрипт отказывается писать в непустую целевую БД. Для осознанной
перезаписи используйте `--force`.
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.postgres_database import PostgresDatabase

_TABLES = (
    "guild_settings",
    "warns",
    "tickets",
    "reminders",
    "polls",
    "poll_votes",
    "giveaways",
    "giveaway_entries",
    "kv",
    "module_settings",
    "temp_voices",
    "birthdays",
    "scheduled_messages",
    "moderation_cases",
    "admin_audit",
    "activity_hourly",
    "events",
    "event_signup",
    "where_play",
    "caller_rooms",
    "dossier_settings",
    "admissions",
    "dossier_drafts",
    "dossiers",
)
_SEQUENCES = {
    "warns": "id",
    "tickets": "ticket_id",
    "reminders": "id",
    "polls": "id",
    "giveaways": "id",
    "scheduled_messages": "id",
    "moderation_cases": "case_id",
    "admin_audit": "id",
    "events": "id",
}


def _sqlite_rows(connection: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
    select_columns = ", ".join(f'"{column}"' for column in columns)
    rows = [tuple(row) for row in connection.execute(f'SELECT {select_columns} FROM "{table}"')]
    return columns, rows


async def migrate(source: Path, target_url: str, force: bool) -> int:
    if not source.is_file():
        raise FileNotFoundError(source)
    sqlite = sqlite3.connect(source)
    sqlite.row_factory = sqlite3.Row
    postgres = PostgresDatabase(target_url)
    await postgres.connect()
    pool = postgres.pool
    if pool is None:
        raise RuntimeError("PostgreSQL pool не создан")
    try:
        async with pool.acquire() as connection:
            non_empty: set[str] = set()
            for table in _TABLES:
                row = await connection.fetchrow(f'SELECT COUNT(*) AS total FROM "{table}"')
                if row and int(row["total"]) > 0:
                    non_empty.add(table)
            if non_empty and not force:
                raise RuntimeError(f"Целевая БД не пуста: {', '.join(sorted(non_empty))}. Используйте --force.")
            if force:
                await connection.execute(
                    "TRUNCATE " + ", ".join(f'"{table}"' for table in reversed(_TABLES)) + " CASCADE"
                )

            async with connection.transaction():
                for table in _TABLES:
                    columns, rows = _sqlite_rows(sqlite, table)
                    if not rows:
                        continue
                    await connection.copy_records_to_table(table, records=rows, columns=columns)
                    print(f"[OK] {table}: {len(rows)}")
                for table, column in _SEQUENCES.items():
                    await connection.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "
                        f"COALESCE(MAX(\"{column}\"), 1), MAX(\"{column}\") IS NOT NULL) FROM \"{table}\""
                    )
    finally:
        sqlite.close()
        await postgres.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="путь к SQLite bot.db")
    parser.add_argument("--target", required=True, help="PostgreSQL DATABASE_URL")
    parser.add_argument("--force", action="store_true", help="очистить таблицы целевой БД перед переносом")
    args = parser.parse_args()
    asyncio.run(migrate(args.source, args.target, args.force))
    print("Миграция SQLite → PostgreSQL завершена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
