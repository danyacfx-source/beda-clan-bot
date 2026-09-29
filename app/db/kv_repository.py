"""Простое key-value хранилище (sticky-сообщения стримов, служебные флаги)."""

from __future__ import annotations

from app.db.base_repository import BaseRepository


class KvRepository(BaseRepository):
    async def get(self, key: str, default: str | None = None) -> str | None:
        row = await self.db.fetchone("SELECT value FROM kv WHERE key = ?", (key,))
        return str(row["value"]) if row else default

    async def set(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def delete(self, key: str) -> bool:
        cursor = await self.db.execute("DELETE FROM kv WHERE key = ?", (key,))
        return cursor.rowcount > 0
