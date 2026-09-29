"""Автозагрузчик когов и регистрация persistent-представлений.

Коги собираются по явной таблице ``COG_PROVIDERS`` (имя параметра конструктора →
сервис из ``bot.services``) вместо рефлексии DI-контейнера. Граница слоёв
(коги не видят репозитории/БД) гарантирована статически: имена параметров
допускают только ``bot`` и ключи таблицы.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from collections.abc import Callable
from typing import TYPE_CHECKING

from discord.ext import commands

from app.cogs import COGS_PACKAGE

if TYPE_CHECKING:
    from app.core.bot import ClanBot
    from app.services import Services

logger = logging.getLogger("bot")


#: Источники зависимостей когов: имя параметра → сервис из bot.services.
#: Параметр ``bot`` подставляется первым позиционным аргументом.
def _services(bot: ClanBot) -> Services:
    services = bot.services
    if services is None:
        raise RuntimeError("Сервисы не собраны до загрузки когов")
    return services


COG_PROVIDERS: dict[str, Callable[[ClanBot], object]] = {
    "settings": lambda b: _services(b).settings,
    "moderation": lambda b: _services(b).moderation,
    "cases": lambda b: _services(b).cases,
    "tickets": lambda b: _services(b).tickets,
    "dossiers": lambda b: _services(b).dossiers,
    "logging": lambda b: _services(b).logging,
    "reminders": lambda b: _services(b).reminders,
    "polls": lambda b: _services(b).polls,
    "giveaways": lambda b: _services(b).giveaways,
    "scheduled": lambda b: _services(b).scheduled,
    "tempvoice": lambda b: _services(b).tempvoice,
    "birthdays": lambda b: _services(b).birthdays,
    "events": lambda b: _services(b).events,
    "where_play": lambda b: _services(b).where_play,
}


def _build_cog(bot: ClanBot, cls: type) -> object:
    """Строит ког по сигнатуре конструктора и таблице ``COG_PROVIDERS``.

    Все обязательные зависимости резолвятся — ``cls(bot, **kwargs)``.
    Иначе (неизвестный обязательный параметр) — откат к ``cls(bot)``.
    """
    params = list(inspect.signature(cls).parameters.values())
    kwargs: dict[str, object] = {}
    resolvable = True
    for param in params:
        if param.name == "bot":
            continue
        if param.name in COG_PROVIDERS:
            kwargs[param.name] = COG_PROVIDERS[param.name](bot)
        elif param.default is not inspect.Parameter.empty:
            continue
        else:
            resolvable = False
            break
    if not resolvable:
        logger.debug("Явная сборка %s невозможна — резерв cls(bot)", cls.__name__)
        return cls(bot)
    return cls(bot, **kwargs)


async def load_cogs(bot: ClanBot) -> list[str]:
    """Обходит app.cogs детерминированно и регистрирует коги по одному.

    - порядок определяется именами модулей (стабильный лог);
    - сбой одного кога не мешает остальным (ошибка логируется);
    - защита от повторной регистрации когов-тёзок.
    """
    loaded: list[str] = []
    failures: list[tuple[str, str]] = []
    used_names: set[str] = set()
    package = importlib.import_module(COGS_PACKAGE)
    modules = sorted(
        (m for m in pkgutil.walk_packages(package.__path__, prefix=f"{COGS_PACKAGE}.") if not m.ispkg),
        key=lambda m: m.name,
    )

    for module in modules:
        try:
            mod = importlib.import_module(module.name)
        except Exception:
            logger.exception("Не удалось импортировать модуль кога %s", module.name)
            failures.append((module.name, "import"))
            continue

        for member_name, member in vars(mod).items():
            if not isinstance(member, type) or not issubclass(member, commands.Cog):
                continue
            if member is commands.Cog or member.__module__ != mod.__name__:
                continue
            if member.__name__ in used_names:
                logger.error("Ког %s пропущен: имя %s уже занято", module.name, member_name)
                failures.append((module.name, f"duplicate:{member_name}"))
                continue
            try:
                instance = _build_cog(bot, member)
                await bot.add_cog(instance)
            except Exception:
                logger.exception("Не удалось загрузить ког %s.%s", module.name, member_name)
                failures.append((module.name, "add_cog"))
                continue
            used_names.add(member.__name__)
            loaded.append(module.name)
            logger.info("Ког загружен: %s.%s", module.name, member_name)

    if failures:
        logger.error("Загружено когов: %d, с ошибками: %d", len(loaded), len(failures))
    else:
        logger.info("Загружено когов: %d", len(loaded))
    return loaded


async def register_persistent_views(bot: ClanBot) -> None:
    """Регистрирует кнопки тикетов, опросов, розыгрышей, ивентов и личных дел, которые переживают рестарт бота."""
    import json

    from app.cogs.administration.dossiers import AdmissionView, ApprovalView
    from app.core.views import (
        EventSignupView,
        GiveawayView,
        PollView,
        TicketCloseView,
        TicketOpenView,
        WherePlayCallerView,
    )

    services = bot.services
    if services is None:
        raise RuntimeError("Сервисы не собраны до регистрации persistent views")
    bot.add_view(TicketOpenView(services.tickets), message_id=None)
    bot.add_view(TicketCloseView(services.tickets), message_id=None)
    bot.add_view(WherePlayCallerView(), message_id=None)
    bot.add_view(AdmissionView(services.dossiers), message_id=None)
    bot.add_view(ApprovalView(services.dossiers), message_id=None)

    giveaway_count = poll_count = event_count = 0
    for giveaway in await services.giveaways.active_with_message():
        try:
            bot.add_view(GiveawayView(), message_id=giveaway["message_id"])
            giveaway_count += 1
        except Exception:
            logger.exception("Не удалось зарегистрировать view розыгрыша #%s", giveaway["id"])
    for poll in await services.polls.active_with_message():
        try:
            option_count = len(json.loads(poll["options"]))
            bot.add_view(PollView(poll["id"], option_count), message_id=poll["message_id"])
            poll_count += 1
        except Exception:
            logger.exception("Не удалось зарегистрировать view опроса #%s", poll["id"])
    for event in await services.events.active_with_message():
        try:
            bot.add_view(EventSignupView(int(event["id"])), message_id=event["message_id"])
            event_count += 1
        except Exception:
            logger.exception("Не удалось зарегистрировать view ивента #%s", event["id"])

    logger.info(
        "Persistent views: тикеты 2, где играем 1, личные дела 2, розыгрыши %d, опросы %d, ивенты %d, всего %d",
        giveaway_count,
        poll_count,
        event_count,
        5 + giveaway_count + poll_count + event_count,
    )
