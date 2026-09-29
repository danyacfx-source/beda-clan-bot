"""Безопасность: модель угроз и проверяемые инварианты склейки.

Переход на composition root НЕ меняет модель безопасности: проверка прав,
валидация и секреты живут в классах app/ и не зависят от того, кто конструирует
объекты. Вклад composition root — в читаемой сборке и в инвариантах, которые
проверяются тестами:

    1. Привилегированные коги получают ровно те сервисы, что собраны в Root.
    2. Коги в принципе не получают репозитории/БД (граница слоёв, которую старый
       контейнер держал динамической табулатурой DENIED, теперь гарантирована
       статически в COG_PROVIDERS).
    3. Секреты только из Config (env), литералов в коде нет.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.root import Root

#: Коги, имеющие полномочия (модерация/автомод/настройка) → (атрибут кога, сервис в Root).
PRIVILEGED_WIRING: dict[str, tuple[tuple[str, str], ...]] = {
    "Moderation": (("moderation", "moderation"), ("logging", "logging")),
    "Warns": (("moderation", "moderation"),),
    "AutoMod": (("settings", "settings"),),
    "Setup": (("settings", "settings"),),
}


def assert_wiring(root: Root) -> None:
    """Гарантирует, что привилегированные коги привязаны к реальным сервисам Root."""
    for cog_name, pairs in PRIVILEGED_WIRING.items():
        cog = root.bot.get_cog(cog_name)
        if cog is None:
            raise RuntimeError(f"привилегированный ког {cog_name} не загружен")
        for attr, svc in pairs:
            got = getattr(cog, attr, None)
            want = getattr(root.services, svc)
            if got is not want:
                raise RuntimeError(f"{cog_name}.{attr} указывает не на root.services.{svc} (got {type(got).__name__})")
