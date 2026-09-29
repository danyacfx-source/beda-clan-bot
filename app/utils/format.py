"""Форматирование текста и русская плюрализация."""

from __future__ import annotations

import datetime as dt

from discord.utils import format_dt


def truncate(text: str, limit: int = 1024) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def plural(n: int, one: str, few: str, many: str) -> str:
    n10 = n % 10
    n100 = n % 100
    if 11 <= n100 <= 19:
        return many
    if n10 == 1:
        return one
    if 2 <= n10 <= 4:
        return few
    return many


def relative(value: dt.datetime) -> str:
    return format_dt(value, style="R")


def tick() -> str:
    return "\u2705"


def cross() -> str:
    return "\u274c"
