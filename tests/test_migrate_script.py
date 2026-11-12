"""Проверки скрипта переноса SQLite → PostgreSQL."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from app.db.database import Database

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_sqlite_to_postgres.py"


def _load():
    spec = importlib.util.spec_from_file_location("migrate_sqlite_to_postgres", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_migration_tables_cover_sqlite_schema(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "bot.db"))
    await db.connect()
    await db.close()
    with sqlite3.connect(tmp_path / "bot.db") as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
    module = _load()
    assert len(module._TABLES) == len(set(module._TABLES))
    assert tables - {"schema_migrations"} == set(module._TABLES)
    assert set(module._SEQUENCES) <= set(module._TABLES)
