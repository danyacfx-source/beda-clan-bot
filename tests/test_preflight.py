"""Проверки вспомогательных функций scripts/preflight.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "preflight.py"
_spec = importlib.util.spec_from_file_location("preflight_script", _SCRIPT)
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


def test_mask_url_hides_password() -> None:
    url = "postgresql://beda:s3cret@127.0.0.1:5432/beda"
    assert preflight.mask_url(url) == "postgresql://beda:***@127.0.0.1:5432/beda"


def test_mask_url_hides_password_with_at_sign() -> None:
    masked = preflight.mask_url("postgresql://beda:p@ss:w0rd@db.local/beda")
    assert "p@ss" not in masked and "w0rd" not in masked
    assert masked == "postgresql://beda:***@db.local/beda"


def test_mask_url_masks_inside_error_text() -> None:
    text = "connect to postgresql://u:pw123@h:5432/d failed"
    assert "pw123" not in preflight.mask_url(text)


def test_mask_url_without_password_is_unchanged() -> None:
    assert preflight.mask_url("postgresql://beda@host/beda") == "postgresql://beda@host/beda"


def test_database_name() -> None:
    assert preflight.database_name("postgresql://u:p@h:5432/postgres?sslmode=require") == "postgres"
    assert preflight.database_name("postgresql://u:p@h/beda") == "beda"


def test_mask_url_hides_query_password() -> None:
    assert preflight.mask_url("postgresql://beda@host/beda?password=s3cret") == "postgresql://beda@host/beda?password=***"
    masked = preflight.mask_url("postgresql://beda@host/beda?sslmode=require&Password=s3cret&x=1")
    assert "s3cret" not in masked
    assert "sslmode=require" in masked and "x=1" in masked


def test_mask_url_hides_userinfo_and_query_password() -> None:
    masked = preflight.mask_url("postgresql://u:pw1@h/d?password=pw2")
    assert "pw1" not in masked and "pw2" not in masked
