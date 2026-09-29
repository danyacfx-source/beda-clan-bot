#!/usr/bin/env python3
"""Проверка окружения перед запуском BEDA Clan Bot.

По умолчанию секреты не обязательны: это позволяет проверить чистую копию
проекта до заполнения `.env`. Для проверки готовности к запуску используйте
`--strict`.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REQUIRED_MODULES = {
    "discord": "discord.py",
    "aiosqlite": "aiosqlite",
    "aiohttp": "aiohttp",
    "dotenv": "python-dotenv",
    "nacl": "PyNaCl",
    "psutil": "psutil",
    "argon2": "argon2-cffi",
}


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def _check_modules() -> list[str]:
    errors: list[str] = []
    for module, package in REQUIRED_MODULES.items():
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"{package}: {exc.__class__.__name__}")
    return errors


def _check_writable(path: Path) -> str | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".preflight-", dir=path, delete=True):
            pass
    except OSError as exc:
        return f"{path}: {exc}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="считать отсутствие BOT_TOKEN ошибкой")
    args = parser.parse_args()
    _load_dotenv()

    errors: list[str] = []
    warnings: list[str] = []
    version = sys.version_info
    if version < (3, 11):
        errors.append(f"Нужен Python 3.11+, найден {version.major}.{version.minor}")
    else:
        print(f"[OK] Python {version.major}.{version.minor}.{version.micro}")

    module_errors = _check_modules()
    errors.extend(module_errors)
    if module_errors:
        for item in module_errors:
            print(f"[FAIL] Импорт: {item}")
    else:
        print("[OK] Python-зависимости")

    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        print("[OK] BOT_TOKEN задан")
    elif args.strict:
        errors.append("BOT_TOKEN не задан")
        print("[FAIL] BOT_TOKEN не задан")
    else:
        warnings.append("BOT_TOKEN пока не задан — это нормально до заполнения .env")
        print("[WARN] BOT_TOKEN не задан")

    db_path = Path(os.getenv("DB_PATH", str(ROOT / "data" / "bot.db")))
    backup_dir = Path(os.getenv("DB_BACKUP_DIR", str(ROOT / "data" / "backups")))
    for label, path in (("DB_PATH", db_path), ("DB_BACKUP_DIR", backup_dir)):
        writable_error = _check_writable(path.parent if path.suffix else path)
        if writable_error:
            errors.append(f"{label}: {writable_error}")
            print(f"[FAIL] {label}: {writable_error}")
        else:
            print(f"[OK] {label}: {path}")

    try:
        from app.config import Config

        if token:
            Config.from_env(ROOT / ".env")
            print("[OK] Конфигурация .env разбирается")
    except Exception as exc:
        errors.append(f"Конфигурация: {exc}")
        print(f"[FAIL] Конфигурация: {exc}")

    if warnings:
        print(f"\nПредупреждений: {len(warnings)}")
    if errors:
        print(f"Ошибок: {len(errors)}")
        return 1
    print("\nПредстартовая проверка завершена успешно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
