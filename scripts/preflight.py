#!/usr/bin/env python3
"""Проверка окружения перед запуском BEDA Clan Bot.

По умолчанию секреты не обязательны: это позволяет проверить чистую копию
проекта до заполнения `.env`. Для проверки готовности к запуску используйте
`--strict`.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

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


_PASSWORD_RE = re.compile(r"(://[^:/@\s?#]*):[^\s?#]*@")
_QUERY_PASSWORD_RE = re.compile(r"([?&](?:password|sslpassword|passfile)=)[^&\s#]*", re.IGNORECASE)


def mask_url(url: str) -> str:
    """Скрывает пароль в URL подключения: postgresql://user:***@host/db."""
    return _QUERY_PASSWORD_RE.sub(r"\1***", _PASSWORD_RE.sub(r"\1:***@", url))


def database_name(url: str) -> str:
    """Имя БД из URL подключения (без параметров запроса)."""
    try:
        return urlsplit(url).path.lstrip("/")
    except ValueError:
        return ""


async def _probe_postgres(url: str) -> tuple[str, int]:
    import asyncpg  # type: ignore[import-untyped]

    connection = await asyncpg.connect(url, timeout=10)
    try:
        version = str(await connection.fetchval("SHOW server_version"))
        tables = int(
            await connection.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        )
    finally:
        await connection.close()
    return version, tables


def _check_postgres(url: str, errors: list[str], warnings: list[str], strict: bool) -> None:
    masked = mask_url(url)
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        errors.append("asyncpg не установлен")
        print("[FAIL] asyncpg не установлен (pip install -r requirements.txt)")
        return
    if database_name(url) == "postgres":
        warnings.append("DATABASE_URL указывает на общую БД postgres")
        print("[WARN] DATABASE_URL указывает на общую БД 'postgres' — создайте отдельную (scripts/pg_create_db.sh)")
    try:
        version, tables = asyncio.run(asyncio.wait_for(_probe_postgres(url), timeout=15))
    except Exception as exc:
        message = mask_url(f"{exc.__class__.__name__}: {exc}")
        if strict:
            errors.append(f"PostgreSQL недоступен: {message}")
            print(f"[FAIL] PostgreSQL {masked}: {message}")
        else:
            warnings.append(f"PostgreSQL недоступен: {message}")
            print(f"[WARN] PostgreSQL {masked}: {message}")
        return
    print(f"[OK] PostgreSQL {version}, {masked}, таблиц в public: {tables}")


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

    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        _check_postgres(database_url, errors, warnings, args.strict)

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
