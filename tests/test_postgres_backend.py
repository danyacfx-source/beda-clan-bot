"""Проверки PostgreSQL-адаптера без обязательного запущенного PostgreSQL."""
from __future__ import annotations

from app.db.postgres_database import _translate_sql


def test_postgres_translates_placeholders_and_generated_id() -> None:
    query, key = _translate_sql("INSERT INTO reminders (user_id, message) VALUES (?, ?)")
    assert query.endswith("RETURNING id")
    assert "$1" in query and "$2" in query
    assert key == "id"


def test_postgres_translates_sqlite_ignore_and_replace() -> None:
    ignore, ignore_key = _translate_sql("INSERT OR IGNORE INTO kv(key, value) VALUES (?, ?)")
    replace, replace_key = _translate_sql(
        "INSERT OR REPLACE INTO poll_votes (poll_id, user_id, option) VALUES (?, ?, ?)"
    )
    assert "INSERT OR IGNORE" not in ignore.upper()
    assert "ON CONFLICT DO NOTHING" in ignore.upper()
    assert ignore_key is None
    assert "EXCLUDED.OPTION" in replace.upper()
    assert replace_key is None
