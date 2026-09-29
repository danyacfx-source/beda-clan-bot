"""Composition root: единственное место, где собирается весь граф зависимостей.

Каждая зависимость создаётся явно в порядке «репозитории → сервисы → сервисы,
зависящие от бота». Порядок здесь и есть граф.

Замена реализации (фейк, другая БД, другой бот) — параметры ``assemble()``,
а не правка потребителей.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import Config
    from app.core.bot import ClanBot
    from app.core.root import Root
    from app.db.database import Database

logger = logging.getLogger("bot")


def assemble(
    *,
    config: Config,
    db: Database,
    bot: ClanBot | None = None,
    #: Точечные подмены реализаций (тесты, переключение инфраструктуры).
    **overrides: Any,
) -> Root:
    """Строит и возвращает Root со всеми сервисами и ботом."""
    from app.core.bot import ClanBot
    from app.db.birthdays_repository import BirthdaysRepository
    from app.db.dossiers_repository import DossiersRepository
    from app.db.events_repository import EventsRepository
    from app.db.giveaways_repository import GiveawaysRepository
    from app.db.kv_repository import KvRepository
    from app.db.moderation_cases_repository import ModerationCasesRepository
    from app.db.module_settings_repository import ModuleSettingsRepository
    from app.db.polls_repository import PollsRepository
    from app.db.reminders_repository import RemindersRepository
    from app.db.scheduled_repository import ScheduledRepository
    from app.db.settings_repository import SettingsRepository
    from app.db.temp_voices_repository import TempVoicesRepository
    from app.db.tickets_repository import TicketsRepository
    from app.db.warns_repository import WarnsRepository
    from app.db.where_play_repository import WherePlayRepository
    from app.services import Services
    from app.services.birthday_service import BirthdayService
    from app.services.dossier_service import DossierService
    from app.services.event_service import EventService
    from app.services.giveaway_service import GiveawayService
    from app.services.logging_service import LoggingService
    from app.services.moderation_case_service import ModerationCaseService
    from app.services.moderation_service import ModerationService
    from app.services.module_settings_service import ModuleSettingsService
    from app.services.poll_service import PollService
    from app.services.reminder_service import ReminderService
    from app.services.scheduler_service import ScheduledMessagesService
    from app.services.settings_service import SettingsService
    from app.services.temp_voice_service import TempVoiceService
    from app.services.ticket_service import TicketService
    from app.services.where_play_service import WherePlayService

    # --- Репозитории (слой данных) ---
    settings_repo = override_or(overrides, "settings_repo", SettingsRepository, db)
    override_or(overrides, "kv_repo", KvRepository, db)
    module_settings_repo = override_or(overrides, "module_settings_repo", ModuleSettingsRepository, db)

    # --- Сервисы, зависящие только от репозиториев ---
    settings = override_or(overrides, "settings", SettingsService, settings_repo)
    module_settings = override_or(overrides, "module_settings", ModuleSettingsService, module_settings_repo, config)
    reminders = override_or(overrides, "reminders", ReminderService, RemindersRepository(db))
    polls = override_or(overrides, "polls", PollService, PollsRepository(db))
    giveaways = override_or(overrides, "giveaways", GiveawayService, GiveawaysRepository(db))
    tempvoice = override_or(overrides, "tempvoice", TempVoiceService, TempVoicesRepository(db))
    birthdays = override_or(overrides, "birthdays", BirthdayService, BirthdaysRepository(db))
    scheduled = override_or(overrides, "scheduled", ScheduledMessagesService, ScheduledRepository(db))
    dossiers = override_or(overrides, "dossiers", DossierService, DossiersRepository(db))

    # --- Бот (нужен сервисам, которые пишут в Discord) ---
    if bot is None:
        bot = ClanBot(config)

    # --- Сервисы, зависящие от бота и/или настроек ---
    logging_svc = override_or(overrides, "logging", LoggingService, settings, bot)
    moderation = override_or(overrides, "moderation", ModerationService, WarnsRepository(db), settings)
    cases = override_or(overrides, "cases", ModerationCaseService, ModerationCasesRepository(db))
    tickets = override_or(overrides, "tickets", TicketService, settings, TicketsRepository(db), logging_svc, config)
    events = override_or(
        overrides,
        "events",
        EventService,
        EventsRepository(db),
        config.events_reminder_lead_minutes,
    )
    where_play = override_or(overrides, "where_play", WherePlayService, WherePlayRepository(db), bot)

    services = Services(
        settings=settings,
        module_settings=module_settings,
        moderation=moderation,
        cases=cases,
        tickets=tickets,
        dossiers=dossiers,
        logging=logging_svc,
        reminders=reminders,
        polls=polls,
        giveaways=giveaways,
        tempvoice=tempvoice,
        birthdays=birthdays,
        scheduled=scheduled,
        events=events,
        where_play=where_play,
    )

    # --- Прошиваем корень в бота (то же, что делал контейнер через supplied) ---
    bot.config = config
    bot.services = services

    from app.core.root import Root

    root = Root(config=config, db=db, bot=bot, services=services)
    bot.root = root
    logger.debug("Composition root собран: сервисов %d", len(Services.__dataclass_fields__))
    return root


def override_or(overrides: dict[str, Any], name: str, cls: type, *args: Any) -> Any:
    """Возвращает явную подмену из ``overrides`` либо создаёт ``cls(*args)``."""
    if name in overrides:
        return overrides[name]
    return cls(*args)
