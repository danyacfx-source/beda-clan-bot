# BEDA Clan Bot

Discord-бот для игрового клана: тикеты, личные дела, модерация, автомод,
временные голосовые каналы, правила-гейт, напоминания, опросы и розыгрыши,
сборы и подбор игроков.

Это копия медийного бота, очищенная от всего, что клану не нужно: донаты,
стримы (Twitch/Kick/VK), музыка, YouTube, ИИ-чат, сезоны, меню ролей, reaction-роли
и соцсети. Подробности удаления — в `CHANGES.md`.

## Возможности

| Раздел | Что внутри |
| --- | --- |
| Поддержка | тикеты с панелью заявок в клан и транскриптами, права категорий, `/rules`-гейт |
| Модерация | `/ban`, `/kick`, `/timeout`, `/purge`, `/slowmode`, варны, кейсы, автомод, антиraid |
| Голос | временные каналы, лимит комнат, передача владения, панель управления |
| Сообщество | опросы, розыгрыши, дни рождения, правила, счётчики сервера |
| Сборы | `/event`, `/event_edit`, `/event_cancel`, `/event_list`, `/event_signup` |
| Игроки | `/setup_where_play`, `/where_play`, `/stop_play`, `/where_play_status`, `/where_play_card` |
| Личные дела | `/setup_dossiers`, `/approve_interview`, `/review_dossier` — анкета в клан и публикация в тред |
| Напоминания | `/remindme`, `/remind`, `/remind_cancel`, `/remind_clear` — личные напоминания по времени |
| Инфраструктура | веб-панель, отложенные сообщения, резервные копии БД, health-диагностика |

Полный список команд и настроек — в [FEATURES.md](FEATURES.md).

## Требования

- Python 3.11+
- `libopus` (голосовые каналы): `apt install libopus0` / `brew install opus`
- Discord-бот с правом `Message Content` и `Server Members`

FFmpeg и yt-dlp больше не нужны.

## Установка

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Windows: copy .env.example .env
```

Заполните `BOT_TOKEN` в `.env`, затем проверьте окружение:

```bash
python scripts/preflight.py          # нестрогая проверка
python scripts/preflight.py --strict # + обязательный BOT_TOKEN
```

## Запуск

```bash
python main.py
```

## Тесты и проверки

```bash
pip install -r requirements-dev.txt
  pytest -q          # 155 тестов
ruff check app main.py scripts tests
```

## Развёртывание

- systemd: `systemd/beda-clan-bot.service`, установка — `scripts/install_ubuntu.sh`
- Docker: `docker compose up -d`
- Миграция на PostgreSQL: `python scripts/migrate_sqlite_to_postgres.py`

Подробности — в [INFRASTRUCTURE.md](INFRASTRUCTURE.md).

## Структура

```
app/
  cogs/<домен>/     # команды и listeners, один пакет на домен
  core/             # бот, composition root, загрузчик когов, health, веб-панель
  db/               # подключение к БД, схема, репозитории
  services/         # бизнес-логика поверх репозиториев
  config.py         # конфигурация из окружения
main.py             # точка входа
```

Правила слоёв: коги не видят репозитории и БД напрямую — только сервисы из
`bot.services`; граф зависимостей собирается в `app/core/composition.py`.
