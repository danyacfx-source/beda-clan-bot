"""Реестр настроек модулей: единственное описание того, что редактируется в панели.

Раньше настройки модулей жили только в ``.env`` и читались когами напрямую,
поэтому из веб-панели их было не поменять. Теперь каждое поле описано здесь
однократно, и оно же является источником правды для трёх вещей:

* форма в панели строится по этому описанию — новый модуль не требует правок JS;
* значение по умолчанию читается из ``Config`` (то есть из ``.env``);
* коги получают «эффективное» значение: переопределение из БД, иначе ``.env``.

Значение из панели хранится на сервер, поэтому один и тот же бот на разных
серверах может вести себя по-разному. Всё, что здесь описано, применяется
без перезапуска бота.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Виды полей. Панель трактует их по-разному, сервис — приводит к этим типам.
BOOL = "bool"
INT = "int"
FLOAT = "float"
TEXT = "text"
JSON = "json"
IDS = "ids"
CHANNEL = "channel"
CATEGORY = "category"
ROLE = "role"


@dataclass(frozen=True, slots=True)
class ModuleField:
    """Одно настраиваемое поле модуля."""

    key: str
    label: str
    kind: str
    #: Атрибут ``Config`` — значение по умолчанию, пока в БД нет переопределения.
    attr: str
    #: Переменная ``.env``, из которой взят дефолт (показывается в панели).
    env: str
    hint: str = ""
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """Модуль бота и его настраиваемые поля."""

    key: str
    title: str
    emoji: str
    description: str
    fields: tuple[ModuleField, ...]


_SPECS: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        key="tempvoice",
        title="Темп-голосовые",
        emoji="🎙️",
        description="Триггер создания комнат и категория, в которой они появляются.",
        fields=(
            ModuleField(
                key="category_id",
                label="Категория для комнат",
                kind=CATEGORY,
                attr="temp_voice_category_id",
                env="TEMP_VOICE_CATEGORY_ID",
                hint="Пусто — комнаты создаются в категории триггера",
            ),
            ModuleField(
                key="trigger_ids",
                label="ID триггеров",
                kind=IDS,
                attr="temp_voice_trigger_ids",
                env="TEMP_VOICE_TRIGGER_IDS",
                hint="Через запятую. Пусто — модуль выключен",
            ),
        ),
    ),
    ModuleSpec(
        key="events",
        title="События",
        emoji="📅",
        description="Напоминания о событиях и лимит активных событий на сервер.",
        fields=(
            ModuleField(
                key="reminder_lead_minutes",
                label="Напоминать заранее, мин",
                kind=INT,
                attr="events_reminder_lead_minutes",
                env="EVENTS_REMINDER_LEAD_MINUTES",
                minimum=1,
                maximum=180,
            ),
            ModuleField(
                key="check_interval_seconds",
                label="Проверка расписания, сек",
                kind=INT,
                attr="events_check_interval_seconds",
                env="EVENTS_CHECK_INTERVAL_SECONDS",
                minimum=15,
                maximum=3600,
            ),
            ModuleField(
                key="max_active_per_guild",
                label="Максимум активных событий",
                kind=INT,
                attr="events_max_active_per_guild",
                env="EVENTS_MAX_ACTIVE_PER_GUILD",
                minimum=1,
                maximum=200,
            ),
        ),
    ),
    ModuleSpec(
        key="server_stats",
        title="Статистика сервера",
        emoji="📊",
        description="Голосовые счётчики участников и онлайна в категории статистики.",
        fields=(
            ModuleField("enabled", "Включён", BOOL, "server_stats_enabled", "SERVER_STATS_ENABLED"),
            ModuleField(
                key="category_id",
                label="Категория счётчиков",
                kind=CATEGORY,
                attr="server_stats_category_id",
                env="SERVER_STATS_CATEGORY_ID",
                hint="Пусто — берётся по имени категории",
            ),
            ModuleField(
                key="category_name",
                label="Название категории",
                kind=TEXT,
                attr="server_stats_category_name",
                env="SERVER_STATS_CATEGORY_NAME",
                hint="Создаётся, если её ещё нет",
            ),
            ModuleField(
                key="update_seconds",
                label="Обновлять раз в секунд",
                kind=INT,
                attr="server_stats_update_seconds",
                env="SERVER_STATS_UPDATE_SECONDS",
                minimum=30,
                maximum=3600,
            ),
            ModuleField(
                key="channels",
                label="Счётчики (JSON)",
                kind=JSON,
                attr="server_stats_channels",
                env="SERVER_STATS_CHANNELS",
                hint='Массив: [{"label":"Участники","emoji":"👥","counter":"members"}]',
            ),
        ),
    ),
    ModuleSpec(
        key="birthdays",
        title="Дни рождения",
        emoji="🎂",
        description="Канал и роль для ежедневного объявления дней рождения.",
        fields=(
            ModuleField(
                key="channel_id",
                label="Канал объявлений",
                kind=CHANNEL,
                attr="birthday_channel_id",
                env="BIRTHDAY_CHANNEL_ID",
                hint="Пусто — модуль выключен",
            ),
            ModuleField(
                key="announce_hour",
                label="Час объявления (0–23)",
                kind=INT,
                attr="birthday_announce_hour",
                env="BIRTHDAY_ANNOUNCE_HOUR",
                minimum=0,
                maximum=23,
            ),
            ModuleField(
                key="ping_role_id",
                label="Роль для пинга",
                kind=ROLE,
                attr="birthday_ping_role_id",
                env="BIRTHDAY_PING_ROLE_ID",
            ),
        ),
    ),
    ModuleSpec(
        key="rules_gate",
        title="Правила",
        emoji="🚧",
        description="Сообщение с правилами и роль, которая даётся за их принятие.",
        fields=(
            ModuleField(
                key="message_id",
                label="ID сообщения с правилами",
                kind=TEXT,
                attr="rules_message_id",
                env="RULES_MESSAGE_ID",
                hint="Оба поля нужны одновременно, иначе гейт выключен",
            ),
            ModuleField(
                key="role_id",
                label="Роль за правила",
                kind=ROLE,
                attr="rules_role_id",
                env="RULES_ROLE_ID",
            ),
        ),
    ),
    ModuleSpec(
        key="ram_report",
        title="Отчёт по памяти",
        emoji="🧠",
        description="Периодический отчёт об использовании оперативной памяти.",
        fields=(
            ModuleField(
                key="channel_id",
                label="Канал отчётов",
                kind=CHANNEL,
                attr="ram_report_channel_id",
                env="RAM_REPORT_CHANNEL_ID",
                hint="Пусто — модуль выключен",
            ),
            ModuleField(
                key="interval_minutes",
                label="Интервал, мин",
                kind=INT,
                attr="ram_report_interval_minutes",
                env="RAM_REPORT_INTERVAL_MINUTES",
                minimum=1,
                maximum=1440,
            ),
            ModuleField("tracemalloc", "Считать трассировку", BOOL, "ram_report_tracemalloc", "RAM_REPORT_TRACEMALLOC"),
        ),
    ),
    ModuleSpec(
        key="where_play",
        title="Где играем",
        emoji="🎯",
        description="Как часто опрашиваются серверы и какие коды принимает команда.",
        fields=(
            ModuleField(
                key="poll_seconds",
                label="Опрос раз в секунд",
                kind=INT,
                attr="where_play_poll_seconds",
                env="WHERE_PLAY_POLL_SECONDS",
                minimum=15,
                maximum=3600,
            ),
            ModuleField(
                key="join_code_min",
                label="Минимальная длина кода",
                kind=INT,
                attr="join_code_min",
                env="JOIN_CODE_MIN",
                minimum=4,
                maximum=32,
            ),
            ModuleField(
                key="join_code_max",
                label="Максимальная длина кода",
                kind=INT,
                attr="join_code_max",
                env="JOIN_CODE_MAX",
                minimum=4,
                maximum=128,
            ),
            ModuleField(
                key="api_url",
                label="Адрес API",
                kind=TEXT,
                attr="where_play_api_url",
                env="WHERE_PLAY_API_URL",
            ),
        ),
    ),
    ModuleSpec(
        key="automod",
        title="Автомод",
        emoji="🛡️",
        description="Пороги автомодерации. Включение и запрещённые слова — на вкладке «Автомод».",
        fields=(
            ModuleField("block_links", "Блокировать ссылки", BOOL, "automod_block_links", "AUTOMOD_BLOCK_LINKS"),
            ModuleField("allowed_links", "Разрешённые домены", TEXT, "automod_allowed_links", "AUTOMOD_ALLOWED_LINKS", "Через запятую"),
            ModuleField("banned_words", "Запрещённые слова (.env)", TEXT, "automod_banned_words", "AUTOMOD_BANNED_WORDS", "Через запятую"),
            ModuleField(
                key="caps_threshold",
                label="Порог капса (0–1)",
                kind=FLOAT,
                attr="automod_caps_threshold",
                env="AUTOMOD_CAPS_THRESHOLD",
            ),
            ModuleField(
                key="caps_min_len",
                label="Мин. длина для капса",
                kind=INT,
                attr="automod_caps_min_len",
                env="AUTOMOD_CAPS_MIN_LEN",
                minimum=1,
                maximum=200,
            ),
            ModuleField(
                key="max_messages_in_window",
                label="Сообщений в окне",
                kind=INT,
                attr="automod_max_messages_in_window",
                env="AUTOMOD_MAX_MESSAGES_IN_WINDOW",
                minimum=0,
                maximum=100,
            ),
            ModuleField(
                key="timeout_seconds",
                label="Таймаут, сек",
                kind=INT,
                attr="automod_timeout_seconds",
                env="AUTOMOD_TIMEOUT_SECONDS",
                minimum=10,
                maximum=86400,
            ),
            ModuleField(
                key="ban_after_timeouts",
                label="Бан после N таймаутов",
                kind=INT,
                attr="automod_ban_after_timeouts",
                env="AUTOMOD_BAN_AFTER_TIMEOUTS",
                minimum=1,
                maximum=50,
            ),
            ModuleField(
                key="ban_window_seconds",
                label="Окно подсчёта таймаутов, сек",
                kind=INT,
                attr="automod_ban_window_seconds",
                env="AUTOMOD_BAN_WINDOW_SECONDS",
                minimum=10,
                maximum=604800,
            ),
            ModuleField("ignore_roles", "Игнорировать роли", IDS, "automod_ignore_roles", "AUTOMOD_IGNORE_ROLES", "ID через запятую"),
            ModuleField(
                key="ignored_channels",
                label="Игнорировать каналы",
                kind=IDS,
                attr="automod_ignored_channels",
                env="AUTOMOD_IGNORED_CHANNELS",
                hint="ID через запятую",
            ),
            ModuleField(
                key="min_account_age_days",
                label="Мин. возраст аккаунта, дней",
                kind=INT,
                attr="automod_min_account_age_days",
                env="AUTOMOD_MIN_ACCOUNT_AGE_DAYS",
                minimum=0,
                maximum=3650,
            ),
            ModuleField("exempt_regex", "Исключения (регулярка)", TEXT, "automod_exempt_regex", "AUTOMOD_EXEMPT_REGEX"),
            ModuleField("antiraid_enabled", "Антирейд", BOOL, "automod_antiraid_enabled", "AUTOMOD_ANTIRAID_ENABLED"),
            ModuleField(
                key="antiraid_window_seconds",
                label="Окно антирейда, сек",
                kind=INT,
                attr="automod_antiraid_window_seconds",
                env="AUTOMOD_ANTIRAID_WINDOW_SECONDS",
                minimum=5,
                maximum=600,
            ),
            ModuleField(
                key="antiraid_join_threshold",
                label="Порог заходов",
                kind=INT,
                attr="automod_antiraid_join_threshold",
                env="AUTOMOD_ANTIRAID_JOIN_THRESHOLD",
                minimum=1,
                maximum=100,
            ),
            ModuleField(
                key="antiraid_slowmode_seconds",
                label="Слоумо после атаки, сек",
                kind=INT,
                attr="automod_antiraid_slowmode_seconds",
                env="AUTOMOD_ANTIRAID_SLOWMODE_SECONDS",
                minimum=0,
                maximum=21600,
            ),
            ModuleField(
                key="antiraid_cooldown_seconds",
                label="Кулдаун антирейда, сек",
                kind=INT,
                attr="automod_antiraid_cooldown_seconds",
                env="AUTOMOD_ANTIRAID_COOLDOWN_SECONDS",
                minimum=0,
                maximum=86400,
            ),
            ModuleField(
                key="lockdown_seconds",
                label="Длительность блокировки, сек",
                kind=INT,
                attr="automod_lockdown_seconds",
                env="AUTOMOD_LOCKDOWN_SECONDS",
                minimum=10,
                maximum=604800,
            ),
        ),
    ),
    ModuleSpec(
        key="permissions",
        title="Права по категориям",
        emoji="🔐",
        description="Автовыдача прав участникам по категориям каналов.",
        fields=(
            ModuleField("auto_apply", "Применять автоматически", BOOL, "permissions_auto_apply", "PERMISSIONS_AUTO_APPLY"),
            ModuleField(
                key="categories",
                label="Категории (JSON)",
                kind=JSON,
                attr="permissions_categories",
                env="PERMISSIONS_CATEGORIES",
                hint='Объект: {"Категория":{"view_channel":true,"send_messages":true}}',
            ),
        ),
    ),
    ModuleSpec(
        key="logs",
        title="Логи",
        emoji="📜",
        description="Какие каналы и категории не попадают в логи.",
        fields=(
            ModuleField(
                key="ignore_channels",
                label="Игнорировать каналы",
                kind=IDS,
                attr="logs_ignore_channel_ids",
                env="LOGS_IGNORE_CHANNEL_IDS",
                hint="ID через запятую",
            ),
            ModuleField(
                key="ignore_categories",
                label="Игнорировать категории",
                kind=IDS,
                attr="logs_ignore_category_ids",
                env="LOGS_IGNORE_CATEGORY_IDS",
                hint="ID через запятую",
            ),
        ),
    ),
)

MODULE_SPECS: tuple[ModuleSpec, ...] = _SPECS
SPEC_BY_KEY: dict[str, ModuleSpec] = {spec.key: spec for spec in _SPECS}


def field_by_key(module_key: str, field_key: str) -> ModuleField | None:
    """Возвращает описание поля или ``None``, если его нет в реестре."""
    spec = SPEC_BY_KEY.get(module_key)
    if spec is None:
        return None
    for field in spec.fields:
        if field.key == field_key:
            return field
    return None


def defaults_for(module_key: str, config: Any) -> dict[str, Any]:
    """Значения по умолчанию, читаемые из ``Config``.

    Отсутствующие или некорректные атрибуты конфига не роняют чтение: поле
    становится ``None``/пустым, чтобы модуль мог работать с частичным конфигом.
    """
    spec = SPEC_BY_KEY[module_key]
    result: dict[str, Any] = {}
    empty = {BOOL: False, IDS: [], TEXT: "", JSON: ""}
    for field in spec.fields:
        value = getattr(config, field.attr, None)
        try:
            result[field.key] = normalize(field, value)
        except ValueError:
            result[field.key] = empty.get(field.kind)
    return result


def all_defaults(config: Any) -> dict[str, dict[str, Any]]:
    return {spec.key: defaults_for(spec.key, config) for spec in _SPECS}


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "on", "да", "yes", "вкл"}:
            return True
        if lowered in {"false", "0", "off", "нет", "no", "выкл", ""}:
            return False
    raise ValueError("ожидалось true или false")


def _to_ids(value: Any) -> list[int]:
    """Список ID из строки ``1, 2``, списка или числа. Пустое значение — пустой список."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        items: list[Any] = list(value)
    else:
        items = [part for part in str(value).replace(";", ",").replace(" ", ",").split(",") if part]
    result: list[int] = []
    for item in items:
        if isinstance(item, bool):
            raise ValueError("ожидался числовой ID")
        if isinstance(item, int):
            number = item
        else:
            text = str(item).strip()
            if not text:
                continue
            if not (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
                raise ValueError(f"«{text}» не похоже на ID")
            number = int(text)
        if number not in result:
            result.append(number)
    return result


def _to_optional_int(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("ожидался числовой ID")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if not (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
        raise ValueError(f"«{text}» не похоже на ID")
    return int(text)


def normalize(field: ModuleField, value: Any) -> Any:
    """Приводит значение к типу поля. Бросает ``ValueError`` с русским текстом."""
    try:
        if field.kind == BOOL:
            return _to_bool(value)
        if field.kind == IDS:
            return _to_ids(value)
        if field.kind in {CHANNEL, CATEGORY, ROLE}:
            return _to_optional_int(value)
        if field.kind == INT:
            number = _to_optional_int(value)
            if number is None:
                raise ValueError("ожидалось целое число")
            if field.minimum is not None and number < field.minimum:
                raise ValueError(f"минимум {field.minimum}")
            if field.maximum is not None and number > field.maximum:
                raise ValueError(f"максимум {field.maximum}")
            return number
        if field.kind == FLOAT:
            try:
                number = float(str(value).strip().replace(",", "."))
            except (TypeError, ValueError) as exc:
                raise ValueError("ожидалось число") from exc
            if field.minimum is not None and number < field.minimum:
                raise ValueError(f"минимум {field.minimum}")
            if field.maximum is not None and number > field.maximum:
                raise ValueError(f"максимум {field.maximum}")
            return number
        if field.kind == JSON:
            text = "" if value is None else str(value).strip()
            if not text:
                return ""
            try:
                json.loads(text)
            except ValueError as exc:
                raise ValueError("ожидался корректный JSON") from exc
            return text
        return "" if value is None else str(value)
    except ValueError as exc:
        raise ValueError(f"{field.label}: {exc}") from exc


def to_jsonable(value: Any) -> Any:
    """Приводит значение к JSON-совместимому виду (tuple -> list)."""
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
