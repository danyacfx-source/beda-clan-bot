#!/usr/bin/env python3
"""Создаёт Argon2-хэш для PANEL_*_PASSWORD_HASH без вывода пароля в shell history."""
from __future__ import annotations

import argparse
import getpass

from argon2 import PasswordHasher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role",
        choices=("owner", "admin", "moderator", "viewer"),
        default="owner",
        help="роль панели",
    )
    args = parser.parse_args()
    password = getpass.getpass("Пароль панели: ")
    confirmation = getpass.getpass("Повторите пароль: ")
    if not password:
        parser.error("Пароль не должен быть пустым")
    if password != confirmation:
        parser.error("Пароли не совпадают")
    print(f"PANEL_{args.role.upper()}_PASSWORD_HASH={PasswordHasher().hash(password)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
