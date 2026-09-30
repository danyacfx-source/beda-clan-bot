"""Тесты миграций, integrity check и консистентного backup SQLite."""

from pathlib import Path

import pytest

from app.db.backup_manager import DatabaseBackupManager
from app.db.database import Database
from app.db.restore import restore_backup


async def test_database_migrations_integrity_and_backup(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"

    database = Database(str(source))
    await database.connect()
    await database.execute("INSERT INTO kv(key, value) VALUES (?, ?)", ("health", "ok"))
    assert await database.integrity_check() == "ok"
    await database.backup(backup)
    await database.close()

    restored = Database(str(backup))
    await restored.connect()
    row = await restored.fetchone("SELECT value FROM kv WHERE key = ?", ("health",))
    assert row is not None and row["value"] == "ok"
    migrations = await restored.fetchall("SELECT version FROM schema_migrations ORDER BY version")
    assert [row["version"] for row in migrations] == list(range(1, 14))
    await restored.increment_activity(1, "2026-09-24T00:00:00+00:00")
    activity = await restored.list_activity(1)
    assert activity[0]["messages"] == 1
    await restored.close()


async def test_old_database_gains_ticket_voice_column(tmp_path: Path) -> None:
    """Апгрейд существующей базы: колонка появляется, данные не теряются."""
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    try:
        # Схема до появления голосовых комнат.
        conn.execute(
            """
            CREATE TABLE tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL UNIQUE,
                creator_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                closed_at TEXT,
                transcript TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO tickets (guild_id, channel_id, creator_id, created_at) "
            "VALUES (1, 500, 42, '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, 'now')",
            [(v,) for v in range(1, 12)],
        )
        conn.commit()
    finally:
        conn.close()

    database = Database(str(path))
    await database.connect()
    try:
        columns = {row["name"] for row in await database.fetchall("PRAGMA table_info(tickets)")}
        assert "voice_channel_id" in columns
        # Старая строка на месте и не потеряла значений.
        row = await database.fetchone("SELECT channel_id, creator_id, voice_channel_id FROM tickets")
        assert row["channel_id"] == 500
        assert row["creator_id"] == 42
        assert row["voice_channel_id"] is None
        versions = [
            int(r["version"])
            for r in await database.fetchall("SELECT version FROM schema_migrations ORDER BY version")
        ]
        assert 12 in versions
    finally:
        await database.close()


async def test_restore_validates_and_keeps_previous_db(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    first = Database(str(source))
    await first.connect()
    await first.execute("INSERT INTO kv(key, value) VALUES ('marker', 'source')")
    await first.backup(target)
    await first.close()

    replacement = tmp_path / "replacement.db"
    second = Database(str(replacement))
    await second.connect()
    await second.execute("INSERT INTO kv(key, value) VALUES ('marker', 'replacement')")
    await second.close()
    previous = restore_backup(replacement, target)
    assert previous is not None and previous.is_file()


async def test_backup_is_atomic_and_replaces_previous_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "nested" / "backup.db"
    database = Database(str(source))
    await database.connect()
    await database.execute("INSERT INTO kv(key, value) VALUES ('marker', 'first')")
    await database.backup(target)
    await database.execute("UPDATE kv SET value = 'second' WHERE key = 'marker'")
    await database.backup(target)
    await database.close()

    restored = Database(str(target))
    await restored.connect()
    row = await restored.fetchone("SELECT value FROM kv WHERE key = 'marker'")
    assert row is not None and row["value"] == "second"
    assert not list(target.parent.glob(".*.tmp"))
    await restored.close()


async def test_backup_manager_keeps_only_configured_retention(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "source.db"))
    await database.connect()
    manager = DatabaseBackupManager(database, tmp_path / "backups", interval_hours=1, retention=2)

    await manager.backup_now()
    await manager.backup_now()
    await manager.backup_now()

    backups = sorted((tmp_path / "backups").glob("bot-*.db"))
    assert len(backups) == 2
    assert manager.status()["last_backup"] is not None
    assert manager.status()["last_error"] is None
    await database.close()


async def test_database_reports_backend(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "source.db"))
    await database.connect()
    assert database.is_postgres is False
    assert database.backend == "sqlite"
    await database.close()

    # PostgreSQL включается только реальным подключением, поэтому проверяем
    # контракт через тот же признак, которым пользуется менеджер бэкапов.
    database._postgres = object()
    assert database.is_postgres is True
    assert database.backend == "postgresql"
    assert DatabaseBackupManager(database, tmp_path / "backups")._suffix == ".dump"


async def test_backup_manager_explains_missing_pg_dump(tmp_path: Path, monkeypatch) -> None:
    database = Database(str(tmp_path / "source.db"))
    await database.connect()
    await database.close()
    database._postgres = object()
    manager = DatabaseBackupManager(database, tmp_path / "backups", interval_hours=1, retention=2)

    async def _missing_tool(destination):
        raise FileNotFoundError(2, "No such file or directory", "pg_dump")

    monkeypatch.setattr(database, "backup", _missing_tool)
    with pytest.raises(RuntimeError, match="pg_dump"):
        await manager.backup_now()
    # Причина попадает в статус, чтобы её было видно в /health.
    assert "pg_dump" in str(manager.status()["last_error"])
    assert manager.status()["last_backup"] is None
