"""Парсинг и форматирование длительности."""

from __future__ import annotations

import re

_TIME_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

_TIME_TOKEN = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)


def parse_duration(text: str) -> int | None:
    """Разбирает строку вида '2m30s' в секунды. Возвращает None, если распарсить нельзя."""
    if not text:
        return None
    total = 0
    matched = False
    for number, unit in _TIME_TOKEN.findall(text):
        matched = True
        total += int(number) * _TIME_UNITS[unit.lower()]
    if not matched:
        return None
    return total


def format_duration(seconds: int | None) -> str:
    """Форматирует секунды в 'Ч:ММ:СС'."""
    if seconds is None or seconds <= 0:
        return "live"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
