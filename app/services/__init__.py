"""Бизнес-логика: сервисы поверх репозиториев.

Сервисы не знают, кто их создал: граф собирается в composition root
(``app/core/composition.py``), где репозитории подставляются явно.
Как добавить сервис:
  1. создать ``app/services/xxx_service.py`` (класс наследует ``BaseService``);
  2. добавить поле в :class:`Services` и строку в ``assemble()``.

Клан-набор: только то, что нужно игровому сообществу. Медийные подсистемы
(донаты, стримы, музыка, ИИ, сезоны, меню ролей, соцсети) удалены.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.announce_service import AnnounceService
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


@dataclass(slots=True)
class Services:
    settings: SettingsService
    announce: AnnounceService
    #: Точечные настройки модулей: БД поверх .env. См. app/core/module_settings.py
    module_settings: ModuleSettingsService
    moderation: ModerationService
    cases: ModerationCaseService
    tickets: TicketService
    dossiers: DossierService
    logging: LoggingService
    reminders: ReminderService
    polls: PollService
    giveaways: GiveawayService
    tempvoice: TempVoiceService
    birthdays: BirthdayService
    scheduled: ScheduledMessagesService
    events: EventService
    where_play: WherePlayService
