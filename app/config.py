"""Конфигурация приложения из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REQUIRED_VARS = ("BOT_TOKEN",)


def _ints(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    return tuple(int(part) for part in value.split(",") if part.strip().isdigit())


def _strs(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(slots=True, frozen=True)
class Config:
    token: str
    prefix: str
    db_path: str
    log_level: str
    status_activity: str
    owner_id: int | None
    version: str = "3.3.0"
    database_url: str | None = None

    # Резервные копии SQLite (для single-server deployment).
    db_backup_dir: str = str(_PROJECT_ROOT / "data" / "backups")
    db_backup_interval_hours: float = 24.0
    db_backup_retention: int = 7

    # Общий сетевой слой внешних API
    api_timeout_seconds: float = 20.0
    api_proxy: str | None = None
    api_max_concurrency: int = 8
    api_circuit_failure_threshold: int = 5
    api_circuit_reset_seconds: float = 60.0

    # Расширенные логи
    logs_ignore_channel_ids: tuple[int, ...] = ()
    logs_ignore_category_ids: tuple[int, ...] = ()
    bot_log_channel_id: int | None = None
    member_log_channel_id: int | None = None
    message_log_channel_id: int | None = None
    voice_log_channel_id: int | None = None
    mod_log_channel_id: int | None = None

    # Правила-гейт
    rules_message_id: int | None = None
    rules_role_id: int | None = None

    # Временные голосовые каналы
    temp_voice_trigger_ids: tuple[int, ...] = ()
    temp_voice_category_id: int | None = None

    # Ивенты (сборы): мастер в ЛС, RSVP и напоминания
    events_reminder_lead_minutes: int = 15
    events_check_interval_seconds: int = 60
    events_max_active_per_guild: int = 25

    # Карточка «Где играем» (источник — публичный снапшот WardogServers)
    where_play_api_url: str = "https://api.wardogservers.com/v1/snapshot"
    where_play_poll_seconds: int = 60
    join_code_min: int = 8
    join_code_max: int = 36

    # Вебпанель конструктора эмбедов
    panel_host: str = "127.0.0.1"
    panel_port: int | None = None
    panel_password: str | None = None
    panel_admin_password: str | None = None
    panel_moderator_password: str | None = None
    panel_viewer_password: str | None = None
    panel_password_hash: str | None = None
    panel_admin_password_hash: str | None = None
    panel_moderator_password_hash: str | None = None
    panel_viewer_password_hash: str | None = None
    panel_public_url: str | None = None
    panel_oauth_client_id: str | None = None
    panel_oauth_client_secret: str | None = None
    panel_oauth_redirect_url: str | None = None
    panel_bridge_token: str | None = None

    # Дни рождения
    birthday_channel_id: int | None = None
    birthday_announce_hour: int = 9
    birthday_ping_role_id: int | None = None

    # Отчёт по ОЗУ (RamReport)
    ram_report_channel_id: int | None = None
    ram_report_interval_minutes: int = 30
    ram_report_tracemalloc: bool = True

    # Права категорий (permissions, как в Node)
    guild_id: int | None = None
    permissions_auto_apply: bool = False
    permissions_categories: str = ""

    # Счётчики сервера (server_stats, как в Node)
    server_stats_enabled: bool = False
    server_stats_category_id: int | None = None
    server_stats_category_name: str = "СТАТИСТИКА"
    server_stats_update_seconds: int = 300
    server_stats_channels: str = ""

    # Авто-модерация (discord_automod, как в Node automod.js)
    automod_enabled: bool = True
    automod_banned_words: str = ""
    automod_block_links: bool = True
    automod_allowed_links: str = ""
    automod_caps_threshold: float = 0.8
    automod_caps_min_len: int = 12
    automod_max_messages_in_window: int = 5
    automod_timeout_seconds: int = 300
    automod_ban_after_timeouts: int = 3
    automod_ban_window_seconds: int = 300
    automod_ignore_roles: tuple[str, ...] = ()
    automod_ignored_channels: tuple[int, ...] = ()
    automod_antiraid_enabled: bool = False
    automod_antiraid_window_seconds: int = 60
    automod_antiraid_join_threshold: int = 8
    automod_antiraid_slowmode_seconds: int = 10
    automod_antiraid_cooldown_seconds: int = 300
    automod_min_account_age_days: int = 0
    automod_exempt_regex: str = ""
    automod_lockdown_seconds: int = 300

    @classmethod
    def from_env(cls, env_file: str | os.PathLike[str] | None = None) -> Config:
        load_dotenv(env_file, override=False)

        missing = [var for var in _REQUIRED_VARS if not os.getenv(var)]
        if missing:
            raise RuntimeError(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")

        owner_raw = os.getenv("OWNER_ID")
        return cls(
            token=os.environ["BOT_TOKEN"],
            prefix=os.getenv("BOT_PREFIX", "!"),
            db_path=os.getenv("DB_PATH", str(_PROJECT_ROOT / "data" / "bot.db")),
            database_url=os.getenv("DATABASE_URL") or None,
            db_backup_dir=os.getenv("DB_BACKUP_DIR", str(_PROJECT_ROOT / "data" / "backups")),
            db_backup_interval_hours=max(1.0, float(os.getenv("DB_BACKUP_INTERVAL_HOURS", "24"))),
            db_backup_retention=max(1, min(90, int(os.getenv("DB_BACKUP_RETENTION", "7")))),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            status_activity=os.getenv("STATUS_ACTIVITY", "играет с кодом"),
            owner_id=int(owner_raw) if owner_raw and owner_raw.isdigit() else None,
            api_timeout_seconds=max(5.0, min(120.0, float(os.getenv("API_TIMEOUT_SECONDS", "20")))),
            api_proxy=os.getenv("API_PROXY") or None,
            api_max_concurrency=max(1, min(64, int(os.getenv("API_MAX_CONCURRENCY", "8")))),
            api_circuit_failure_threshold=max(1, min(20, int(os.getenv("API_CIRCUIT_FAILURE_THRESHOLD", "5")))),
            api_circuit_reset_seconds=max(5.0, min(900.0, float(os.getenv("API_CIRCUIT_RESET_SECONDS", "60")))),
            logs_ignore_channel_ids=_ints(os.getenv("LOGS_IGNORE_CHANNEL_IDS")),
            logs_ignore_category_ids=_ints(os.getenv("LOGS_IGNORE_CATEGORY_IDS")),
            bot_log_channel_id=_single_int(os.getenv("BOT_LOG_CHANNEL_ID")),
            member_log_channel_id=_single_int(os.getenv("MEMBER_LOG_CHANNEL_ID")),
            message_log_channel_id=_single_int(os.getenv("MESSAGE_LOG_CHANNEL_ID")),
            voice_log_channel_id=_single_int(os.getenv("VOICE_LOG_CHANNEL_ID")),
            mod_log_channel_id=_single_int(os.getenv("MOD_LOG_CHANNEL_ID")),
            rules_message_id=_single_int(os.getenv("RULES_MESSAGE_ID")),
            rules_role_id=_single_int(os.getenv("RULES_ROLE_ID")),
            temp_voice_trigger_ids=_ints(os.getenv("TEMP_VOICE_TRIGGER_IDS")),
            temp_voice_category_id=_single_int(os.getenv("TEMP_VOICE_CATEGORY_ID")),
            events_reminder_lead_minutes=max(1, min(180, int(os.getenv("EVENTS_REMINDER_LEAD_MINUTES", "15")))),
            events_check_interval_seconds=max(15, int(os.getenv("EVENTS_CHECK_INTERVAL_SECONDS", "60"))),
            events_max_active_per_guild=max(1, min(200, int(os.getenv("EVENTS_MAX_ACTIVE_PER_GUILD", "25")))),
            where_play_api_url=os.getenv("WHERE_PLAY_API_URL", "https://api.wardogservers.com/v1/snapshot"),
            where_play_poll_seconds=max(15, int(os.getenv("WHERE_PLAY_POLL_SECONDS", "60"))),
            join_code_min=max(4, min(32, int(os.getenv("JOIN_CODE_MIN", "8")))),
            join_code_max=max(8, min(64, int(os.getenv("JOIN_CODE_MAX", "36")))),
            panel_host=os.getenv("PANEL_HOST", "127.0.0.1"),
            panel_port=_single_int(os.getenv("PANEL_PORT")),
            panel_password=os.getenv("PANEL_PASSWORD"),
            panel_admin_password=os.getenv("PANEL_ADMIN_PASSWORD"),
            panel_moderator_password=os.getenv("PANEL_MODERATOR_PASSWORD"),
            panel_viewer_password=os.getenv("PANEL_VIEWER_PASSWORD"),
            panel_password_hash=os.getenv("PANEL_PASSWORD_HASH") or None,
            panel_admin_password_hash=os.getenv("PANEL_ADMIN_PASSWORD_HASH") or None,
            panel_moderator_password_hash=os.getenv("PANEL_MODERATOR_PASSWORD_HASH") or None,
            panel_viewer_password_hash=os.getenv("PANEL_VIEWER_PASSWORD_HASH") or None,
            panel_public_url=os.getenv("PANEL_PUBLIC_URL"),
            panel_oauth_client_id=os.getenv("PANEL_OAUTH_CLIENT_ID") or None,
            panel_oauth_client_secret=os.getenv("PANEL_OAUTH_CLIENT_SECRET") or None,
            panel_oauth_redirect_url=os.getenv("PANEL_OAUTH_REDIRECT_URL") or None,
            panel_bridge_token=os.getenv("PANEL_BRIDGE_TOKEN") or None,
            birthday_channel_id=_single_int(os.getenv("BIRTHDAY_CHANNEL_ID")),
            birthday_announce_hour=_clamp_hour(os.getenv("BIRTHDAY_ANNOUNCE_HOUR", "9")),
            birthday_ping_role_id=_single_int(os.getenv("BIRTHDAY_PING_ROLE_ID")),
            ram_report_channel_id=_single_int(os.getenv("RAM_REPORT_CHANNEL_ID")),
            ram_report_interval_minutes=max(1, int(os.getenv("RAM_REPORT_INTERVAL_MINUTES", "30"))),
            ram_report_tracemalloc=_bool(os.getenv("RAM_REPORT_TRACEMALLOC", "1")),
            guild_id=_single_int(os.getenv("GUILD_ID")),
            permissions_auto_apply=_bool(os.getenv("PERMISSIONS_AUTO_APPLY")),
            permissions_categories=os.getenv("PERMISSIONS_CATEGORIES", ""),
            server_stats_enabled=_bool(os.getenv("SERVER_STATS_ENABLED")),
            server_stats_category_id=_single_int(os.getenv("SERVER_STATS_CATEGORY_ID")),
            server_stats_category_name=os.getenv("SERVER_STATS_CATEGORY_NAME", "СТАТИСТИКА"),
            server_stats_update_seconds=max(60, int(os.getenv("SERVER_STATS_UPDATE_SECONDS", "300"))),
            server_stats_channels=os.getenv("SERVER_STATS_CHANNELS", ""),
            automod_enabled=_bool(os.getenv("AUTOMOD_ENABLED", "1")),
            automod_banned_words=os.getenv("AUTOMOD_BANNED_WORDS", ""),
            automod_block_links=_bool(os.getenv("AUTOMOD_BLOCK_LINKS", "1")),
            automod_allowed_links=os.getenv("AUTOMOD_ALLOWED_LINKS", ""),
            automod_caps_threshold=float(os.getenv("AUTOMOD_CAPS_THRESHOLD", "0.8")),
            automod_caps_min_len=max(1, int(os.getenv("AUTOMOD_CAPS_MIN_LEN", "12"))),
            automod_max_messages_in_window=max(1, int(os.getenv("AUTOMOD_MAX_MESSAGES_IN_WINDOW", "5"))),
            automod_timeout_seconds=max(0, int(os.getenv("AUTOMOD_TIMEOUT_SECONDS", "300"))),
            automod_ban_after_timeouts=max(0, int(os.getenv("AUTOMOD_BAN_AFTER_TIMEOUTS", "3"))),
            automod_ban_window_seconds=max(1, int(os.getenv("AUTOMOD_BAN_WINDOW_SECONDS", "300"))),
            automod_ignore_roles=_strs(os.getenv("AUTOMOD_IGNORE_ROLES")),
            automod_ignored_channels=_ints(os.getenv("AUTOMOD_IGNORED_CHANNELS")),
            automod_antiraid_enabled=_bool(os.getenv("AUTOMOD_ANTIRAID_ENABLED", "0")),
            automod_antiraid_window_seconds=max(10, int(os.getenv("AUTOMOD_ANTIRAID_WINDOW_SECONDS", "60"))),
            automod_antiraid_join_threshold=max(2, int(os.getenv("AUTOMOD_ANTIRAID_JOIN_THRESHOLD", "8"))),
            automod_antiraid_slowmode_seconds=max(0, int(os.getenv("AUTOMOD_ANTIRAID_SLOWMODE_SECONDS", "10"))),
            automod_antiraid_cooldown_seconds=max(30, int(os.getenv("AUTOMOD_ANTIRAID_COOLDOWN_SECONDS", "300"))),
            automod_min_account_age_days=max(0, int(os.getenv("AUTOMOD_MIN_ACCOUNT_AGE_DAYS", "0"))),
            automod_exempt_regex=os.getenv("AUTOMOD_EXEMPT_REGEX", ""),
            automod_lockdown_seconds=max(30, int(os.getenv("AUTOMOD_LOCKDOWN_SECONDS", "300"))),
        )


def _clamp_hour(value: str) -> int:
    try:
        return max(0, min(23, int(value)))
    except ValueError:
        return 9


def _single_int(value: str | None) -> int | None:
    if value and value.strip().isdigit():
        return int(value.strip())
    return None


def _bool(value: str | None, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "да")
