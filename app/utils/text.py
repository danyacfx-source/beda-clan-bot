"""Нормализация и безопасное усечение пользовательского ввода.

Логика намеренно вынесена из Discord: так её можно покрыть обычными
юнит-тестами без подключения к gateway.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "clip",
    "normalize_space",
    "safe_component",
    "strip_invisible",
    "utf16_len",
]

_CONTROL: re.Pattern[str] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE: re.Pattern[str] = re.compile(r"\s+")
_WINDOWS_RESERVED: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def utf16_len(value: str) -> int:
    """Длина строки в кодовых единицах UTF-16 — именно так её считает Discord."""
    return len(value.encode("utf-16-le")) // 2


def clip(value: str, maximum: int) -> str:
    """Обрезать до ``maximum`` UTF-16 единиц, не разрывая суррогатную пару."""
    if utf16_len(value) <= maximum:
        return value
    return value.encode("utf-16-le")[: maximum * 2].decode("utf-16-le", errors="ignore")


def strip_invisible(value: str) -> str:
    """Убрать управляющие, форматные и комбинирующие символы.

    Вычищаются целые классы Unicode ``Cf``/``Cc``/``Mn``/``Me``, поэтому сюда
    попадают bidi-переопределения, zero-width joiner и tag-символы.
    """
    out: list[str] = []
    for char in value:
        category = unicodedata.category(char)
        if category in ("Cf", "Cc", "Mn", "Me"):
            continue
        out.append(" " if category == "Zs" else char)
    return "".join(out)


def normalize_space(value: object) -> str:
    """Схлопнуть пробелы, вычистить управляющие и невидимые символы.

    Не даёт двум визуально одинаковым ответам разойтись в базе и обходит
    попытки подмешать невидимые символы к значениям.
    """
    if value is None:
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", strip_invisible(str(value)))).strip()


def safe_component(value: str) -> str:
    """Свести значение к одному безопасному элементу пути.

    Вырезает разделители каталогов, букву диска, обходы каталогов, NUL и
    зарезервированные имена Windows. Используется перед превращением
    пользовательского ввода в имя файла.
    """
    cleaned = _CONTROL.sub("", strip_invisible(str(value)))
    cleaned = re.sub(r"\.{2,}", "_", cleaned)
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", cleaned).strip(". ")
    if not cleaned:
        return "file"
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return clip(cleaned, 100)
