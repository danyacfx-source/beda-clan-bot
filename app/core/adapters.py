"""Адаптеры портов: реальные классы app/ как реализации интерфейсов.

Ничего не переопределяется: реальные сервисы/репозитории и есть адаптеры.
Здесь они сводятся к портам и структурно проверяются, чтобы «работает ли
подмена фейком» гарантировалось тестом, а не соглашением.
"""

from __future__ import annotations

from app.core.ports import KvStore, LogSink, SettingsStore
from app.db.kv_repository import KvRepository
from app.services.logging_service import LoggingService
from app.services.settings_service import SettingsService

#: Все реальные классы обязаны структурно соответствовать своим портам.
PORT_MAP: tuple[tuple[type, type], ...] = (
    (KvRepository, KvStore),
    (SettingsService, SettingsStore),
    (LoggingService, LogSink),
)


def assert_ports() -> None:
    """Падает, если реальный класс перестал соответствовать своему порту."""
    for concrete, port in PORT_MAP:
        if not isinstance(concrete, port):
            raise RuntimeError(f"{concrete.__name__} не реализует {port.__name__} (методы порта перестали совпадать с реальным классом)")
