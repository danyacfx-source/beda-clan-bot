"""Данные конфигурации сервера для setup-команд (портировано из Node config.json)."""

from __future__ import annotations

#: Роли настроек: имя роли → (цвет, порядок, права).
ROLE_SETTINGS: dict[str, dict[str, object]] = {
    "Master": {"color": "#e74c3c", "order": 1, "permissions": []},
    "Veteran": {"color": "#9b59b6", "order": 2, "permissions": []},
    "Raider": {"color": "#e67e22", "order": 3, "permissions": []},
    "Regular": {"color": "#3498db", "order": 4, "permissions": []},
    "Local": {"color": "#2ecc71", "order": 5, "permissions": []},
    "Tourist": {"color": "#f1c40f", "order": 6, "permissions": []},
    "Newcomer": {"color": "#95a5a6", "order": 7, "permissions": []},
    "Стрим-оповещения": {"color": "#7289da", "order": 8, "permissions": []},
    "Сборки": {"color": "#f39c12", "order": 9, "permissions": []},
    "Сборщик": {"color": "#00e5ff", "order": 10, "permissions": []},
    "СБЭУ шприц": {"color": "#ff6b6b", "order": 11, "permissions": []},
    "Чемпион сезона": {"color": "#ffd700", "order": 12, "permissions": []},
    "Серебро сезона": {"color": "#c0c0c0", "order": 13, "permissions": []},
    "Бронза сезона": {"color": "#cd7f32", "order": 14, "permissions": []},
    "Tarkov": {"color": "#ad6242", "order": 15, "permissions": []},
    "SQUAD": {"color": "#2ea2cc", "order": 16, "permissions": []},
    "War Thunder": {"color": "#f8a800", "order": 17, "permissions": []},
    "Minecraft": {"color": "#3faf46", "order": 18, "permissions": []},
}

#: Дополнительные роли (extra_roles).
EXTRA_ROLES: tuple[str, ...] = (
    "Сборщик",
    "СБЭУ шприц",
    "Стрим-оповещения",
    "Сборки",
    "Чемпион сезона",
    "Серебро сезона",
    "Бронза сезона",
    "Tarkov",
    "SQUAD",
    "War Thunder",
    "Minecraft",
)

#: Схема категорий: имя категории → настройки (type: text/temp/sponsor).
CHANNEL_CATEGORIES: dict[str, dict[str, object]] = {
    "Tarkov": {"type": "text", "channels": ["общий", "оффтоп", "мемы"]},
    "РЕЙДЫ": {"type": "temp", "create": "➕ Создать канал", "prefix": "Сбор рейда"},
    "ГОЛОС": {"type": "temp", "create": "➕ Создать канал", "prefix": "Разговорный"},
    "СПОНСОРЫ": {
        "type": "sponsor",
        "roles": ["Сборщик", "СБЭУ шприц"],
        "text_channels": ["спонсор-чат"],
        "voice_channels": ["Спонсорский войс"],
    },
    "SQUAD": {"type": "text", "channels": ["squad-chat", "squad-teams"], "voice_channels": ["➕ Создать канал"]},
    "War Thunder": {"type": "text", "channels": ["wt-chat", "wt-squadron"], "voice_channels": ["➕ Создать канал"]},
    "Minecraft": {"type": "text", "channels": ["minecraft-chat", "minecraft-builds"], "voice_channels": ["➕ Создать канал"]},
    "Для не определившихся": {
        "type": "text",
        "channels": ["общий", "общий-18"],
        "voice_channels": ["Общий 1", "Общий 2"],
    },
}

#: Префиксы временных голосовых каналов из схемы категорий.
TEMP_PREFIXES: tuple[str, ...] = ("Сбор рейда", "Разговорный")
