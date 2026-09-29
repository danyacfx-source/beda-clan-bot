"""Группы команд должны быть атрибутами класса кога.

Объявленная на уровне модуля ``app_commands.Group`` не попадает в дерево
команд: подкоманды теряются без единой ошибки, и бот синхронизирует команду
меньше, чем есть в коде. Тест ловит именно такую ошибку.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

COGS_DIR = Path(__file__).resolve().parents[1] / "app" / "cogs"

#: Группы, которые обязаны быть видны в дереве команд.
EXPECTED_GROUPS = {
    "ticket": {"panel", "info"},
    "setup": {"log-channel", "log-type", "ticket-category", "unset", "show"},
    "birthday": {"list", "remove", "set"},
}


def _module_level_groups(path: Path) -> list[str]:
    """Имена групп, объявленных на верхнем уровне модуля (вне класса)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Call) and "Group" in ast.unparse(node.value.func)):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found.append(target.id)
    return found


def _all_cog_modules() -> list[Path]:
    return sorted(p for p in COGS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_cog_modules_exist() -> None:
    assert _all_cog_modules(), "не найдено ни одного модуля кога"


@pytest.mark.parametrize("path", _all_cog_modules(), ids=lambda p: p.name)
def test_groups_are_class_attributes(path: Path) -> None:
    assert not _module_level_groups(path), (
        f"{path.name}: группа объявлена на уровне модуля и потеряется при "
        f"регистрации кога — перенесите её в класс кога атрибутом"
    )


@pytest.mark.parametrize("name,subcommands", sorted(EXPECTED_GROUPS.items()))
def test_group_subcommands_are_declared(name: str, subcommands: set[str]) -> None:
    """Каждая ожидаемая подкоманда реально присутствует в коде."""
    from app.cogs.administration import setup as setup_mod
    from app.cogs.administration import tickets as tickets_mod
    from app.cogs.birthdays import birthdays as birthdays_mod

    modules = {
        "ticket": tickets_mod,
        "setup": setup_mod,
        "birthday": birthdays_mod,
    }
    module = modules[name]
    declared = {
        node.name
        for obj in vars(module).values()
        if isinstance(obj, type)
        for node in vars(obj).values()
        if getattr(node, "parent", None) is not None and node.parent.name == name
    }
    assert declared == subcommands, f"подкоманды /{name} разошлись с ожиданием"
