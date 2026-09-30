"""Ошибки использования API discord.py, которые не ловятся типами.

В discord.py 2.x у ``discord.Interaction`` нет атрибута ``.bot`` — есть только
``.client``. Обращение ``interaction.bot.config`` падает с ``AttributeError``
прямо в колбэке. Если к этому моменту interaction уже отложен через ``defer``,
пользователь видит «думалку» навсегда: ответить больше некому.

Тест ловит именно такое обращение во всём ``app/``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import discord
import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _interaction_attribute_accesses(path: Path) -> list[str]:
    """Все обращения вида ``interaction.<что-то>`` в модуле."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "interaction"
        ):
            found.append(node.attr)
    return found


def _all_modules() -> list[Path]:
    return sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_interaction_really_has_no_bot_attribute() -> None:
    """Документирует причину: полагаться на ``interaction.bot`` нельзя."""
    assert not hasattr(discord.Interaction, "bot")
    assert hasattr(discord.Interaction, "client")


@pytest.mark.parametrize("path", _all_modules(), ids=lambda p: p.name)
def test_no_interaction_bot_attribute(path: Path) -> None:
    assert "bot" not in _interaction_attribute_accesses(path), (
        f"{path.name}: обращение interaction.bot — в discord.py 2.x такого "
        f"атрибута нет, используйте interaction.client"
    )
