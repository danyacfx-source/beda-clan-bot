"""Слой данных: подключение к БД и репозитории."""

from app.db.birthdays_repository import BirthdaysRepository
from app.db.events_repository import EventsRepository
from app.db.giveaways_repository import GiveawaysRepository
from app.db.kv_repository import KvRepository
from app.db.moderation_cases_repository import ModerationCasesRepository
from app.db.polls_repository import PollsRepository
from app.db.reminders_repository import RemindersRepository
from app.db.scheduled_repository import ScheduledRepository
from app.db.settings_repository import SettingsRepository
from app.db.temp_voices_repository import TempVoicesRepository
from app.db.tickets_repository import TicketsRepository
from app.db.warns_repository import WarnsRepository
from app.db.where_play_repository import WherePlayRepository

REPOSITORY_CLASSES: tuple[type, ...] = (
    SettingsRepository,
    WarnsRepository,
    ModerationCasesRepository,
    TicketsRepository,
    RemindersRepository,
    PollsRepository,
    GiveawaysRepository,
    ScheduledRepository,
    KvRepository,
    TempVoicesRepository,
    BirthdaysRepository,
    EventsRepository,
    WherePlayRepository,
)
