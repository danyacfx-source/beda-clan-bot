"""Безопасное восстановление SQLite backup при остановленном приложении."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def validate_backup(source: str | Path) -> None:
    """Проверяет backup через SQLite integrity_check до замены рабочего файла."""
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    connection = sqlite3.connect(source_path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"SQLite backup повреждён: {result[0] if result else 'unknown'}")
    finally:
        connection.close()


def restore_backup(source: str | Path, target: str | Path, keep_backup: bool = True) -> Path | None:
    """Атомарно заменяет target проверенным backup и возвращает путь старого файла."""
    source_path = Path(source)
    target_path = Path(target)
    validate_backup(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    old_backup: Path | None = None
    if target_path.exists() and keep_backup:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        old_backup = target_path.with_name(f"{target_path.name}.before-restore-{stamp}.bak")
        shutil.copy2(target_path, old_backup)
    temporary = target_path.with_suffix(target_path.suffix + ".restore.tmp")
    shutil.copy2(source_path, temporary)
    validate_backup(temporary)
    temporary.replace(target_path)
    return old_backup
