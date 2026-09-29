"""Кольцевой буфер логов для вкладки «Логи» вебпанели и страницы /logs."""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime

_AUDIT_PREFIX = "bot.services.audit."


class RingBufferHandler(logging.Handler):
    """Хранит последние N записей лога в памяти (не блокирует приложение)."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__(level=logging.INFO)
        self.capacity = capacity
        self._records: deque[dict[str, str]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
            text = record.getMessage()
            if len(text) > 2000:
                text = text[:2000] + "…"
            name = record.name
            is_audit = name.startswith(_AUDIT_PREFIX)
            cat = name[len(_AUDIT_PREFIX) :] if is_audit else "sys"
            entry = {
                "t": ts,
                "level": record.levelname,
                "name": name,
                "msg": text,
                "cat": cat,
                "audit": "1" if is_audit else "0",
            }
            with self._lock:
                self._records.append(entry)
        except Exception:
            pass

    def snapshot(
        self,
        limit: int | None = None,
        *,
        level: str = "",
        cat: str = "",
        audit: str = "",
    ) -> list[dict[str, str]]:
        """audit="1" — только события Discord, audit="0" — только технические, "" — все."""
        with self._lock:
            items = list(self._records)
        if audit == "1":
            items = [e for e in items if e.get("audit") == "1"]
        elif audit == "0":
            items = [e for e in items if e.get("audit") == "0"]
        if cat:
            items = [e for e in items if e.get("cat") == cat]
        if level:
            items = [e for e in items if e.get("level") == level]
        if limit:
            items = items[-limit:]
        return items
