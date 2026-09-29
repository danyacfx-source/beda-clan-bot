#!/usr/bin/env python3
"""Restore a validated SQLite backup. Stop the bot before running this command."""
from __future__ import annotations

import argparse

from app.db.restore import restore_backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to a SQLite backup")
    parser.add_argument("--target", required=True, help="Path to the bot database")
    parser.add_argument("--no-keep-backup", action="store_true", help="Do not save the current DB before replacing it")
    args = parser.parse_args()
    previous = restore_backup(args.source, args.target, keep_backup=not args.no_keep_backup)
    print(f"Restored {args.source} -> {args.target}")
    if previous:
        print(f"Previous database saved as {previous}")


if __name__ == "__main__":
    main()
