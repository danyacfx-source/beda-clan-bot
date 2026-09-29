# Отчёт об изменениях: BEDA Clan Bot

Дата: 2026-09-28
Источник: `discord-mega-bot` (медийный бот) → результат: `beda-clan-bot`

Копия медийного бота, очищенная до clan-набора. Ниже — что удалено, что
изменено в связях, что исправлено и как это проверено.

## 1. Удалённые подсистемы

| Подсистема | Что удалено |
| --- | --- |
| Донаты | `cogs/donations`, `donation_service`, `donation_repository`, порты/адаптеры DonationAlerts, таблица `donations`, настройка `donation_channel_id`, ключи `DONATION_*` |
| Стримы | `cogs/streams` целиком: Twitch (`TwitchStatus`, живой-эмбед), Kick (стримы+модерация), VK Видео, `core/stream_state.py`, сервисы/репозитории Twitch, Kick, VK Video |
| Музыка | `cogs/music`, `app/services/audio/` (плеер, резолвер, Spotify, трек), `music_service`, `music_repository`, `yt-dlp` (резолвер ссылок), FFmpeg из Docker и установщика, таблицы `music_queue`, `music_playlists`, `music_playlist_tracks`, `music_history` |
| YouTube | отдельного модуля не было — YouTube был частью музыкального резолвера и `/socials`; удалён вместе с ними |
| Соцсети | `cogs/socials`, `/socials` |
| Сезоны | `cogs/seasons`, `season_service`, `season_repository`, таблица `season_points`, `/season` |
| Меню ролей | `cogs/role_menu` |
| ИИ-чат | `cogs/ai`, Gemini-клиент, `/ask`, ключи `AI_*`, `SINGBOX_CONFIG_B64` |
| Reaction-роли | `cogs/reaction_roles`, `reaction_roles_service`, `reaction_roles_repository`, таблица `reaction_roles` |
| Оверлей OBS | `app/core/overlay/`, `OverlayServer`, ключи `OVERLAY_*` |
| WARDOGS | `cogs/wardogs`, `wardogs_service`, страница подключения, ключи `WARDOGS_*` (по отдельному решению, п. 6) |
| Legacy | пакет `rewrite/` (его README предписывал удаление) |

Удалено 53 Python-файла, добавлен 1 (`app/core/presence.py`).
Тесты удалены вместе с кодом: `test_rewrite.py`, `test_music_player.py`,
`test_donation_routing.py`, `test_port.py`, `test_wardogs_service.py`.

## 2. Пересобранные связи

Это главная часть работы: удалённые модули были связаны через контейнер
зависимостей, поэтому чистки по каталогам недостаточно.

- `app/services/__init__.py` — из графа убраны `music`, `donations`,
  `twitch`, `kick`, `vk_video`, `seasons`, `reaction_roles`, `ai_chat`.
- `app/core/composition.py` — удалены их репозитории и сервисы; `Services`
  теперь содержит только живые зависимости.
- `app/core/loader.py` — `COG_PROVIDERS` без media-когов, `EXTENSIONS` без
  FFMPEG/YouTube/dotenv-дока.
- `app/core/ports.py`, `app/core/adapters.py` — удалены порты внешних API
  (Twitch, Kick, DonationAlerts, Spotify, Gemini) и их адаптеры.
- `app/core/bot.py` — убраны `music`-cog, overlay-менеджер и `stream_state`;
  вместо них `presence.py` со статусом активности, заданной `STATUS_ACTIVITY`
  (прежний показывал «Стрим офлайн»).
- `app/core/health.py` — удалены проверки внешних медиа-API, оставлены
  Discord, БД, голос, память, rate limiter, circuit breakers.
- `app/core/webpanel/` — удалены AI-эндпоинты и медийные блоки панели.
- `app/config.py` — удалено 65 полей из 156, новых не добавлено:
  `ai_*` (9), `donat*`/`donate_*` (12), `kick_*` (9), `twitch_*` (6),
  `vk_*` (4), `overlay_*` (9), `role_menu_*` (5), `season_*` (3),
  `socials_*` (5), `spotify_*` (2) — плюс связанные с ними. Полей вида
  `music_*` и `reaction_role_*` в `Config` не было.

## 3. Найденные и исправленные дефекты

Эти ошибки существовали в исходном боте и не были видны без запуска:

1. **Панель падала при старте.** `webpanel/routes.py` регистрировал
   `/api/ai` и `POST /api/ai` на удалённые методы `_api_ai_get` /
   `_api_ai_post`. Любой запуск с включённой панелью давал
   `AttributeError: 'WebPanel' object has no attribute '_api_ai_get'`.
   Роуты удалены; проверено, что все 63 метода панели имеют роут и ни один
   роут не ссылается на отсутствующий метод.
2. **Тесты DI и smoke ссылались на удалённое.** `tests/test_di.py` импортировал
   `KickService`, `test_smoke.py` — `ReactionRolesCog` и `GuildPlayer`.
   Тесты переписаны под фактический состав и теперь дополнительно проверяют,
   что удалённых сервисов (`music`, `donations`, `twitch`, `kick`, `seasons`,
   `reaction_roles`) в графе нет.
3. **`/help` показывал пустую категорию медиа.** Удалена категория, команды
   перенесены в `tools`; набор категорий теперь
   `general / moderation / community / support / tools`.
4. **Линтер.** Убраны неиспользуемые `kv_repo` в `composition.py` и
   `shutil` в `scripts/preflight.py`.
5. **`.env.example` содержал пустые секции** удалённых модулей (донаты,
   Twitch, Kick, VK, оверлей, меню ролей, ИИ, соцсети, сезоны) — оставлены
   заголовки без содержимого, удалены.

## 4. Инфраструктура

- `Dockerfile` — убраны `ffmpeg`, `sing-box`, apt-пакеты для музыки.
- `docker compose` — панель внутри внутренней сети, публичный reverse proxy
  (Traefik, домен `dendich.ru`) удалён.
- `systemd/discord-mega-bot.service` → `systemd/beda-clan-bot.service`.
- `scripts/install_ubuntu.sh`, `scripts/docker-entrypoint.sh` — без FFmpeg и
  sing-box.
- `requirements.txt` — удалён `yt-dlp`.
- `scripts/preflight.py` — проверяет версию Python, импорт зависимостей
  (discord.py, aiosqlite, aiohttp, dotenv, PyNaCl, psutil, argon2), наличие
  `BOT_TOKEN` (в `--strict` — обязательно), права на запись в каталоги БД и
  бэкапов и разбор `.env`. Проверки FFmpeg/yt-dlp/sing-box убраны.

## 5. Что сохранено

Поддержка (тикеты с транскриптами), правила-гейт, модерация и автомод,
антиraid, варны и кейсы, расширенные логи, временные голосовые каналы,
напоминания, опросы, розыгрыши, дни рождения, счётчики сервера, snipe,
конструктор эмбедов, расписание, утилиты, веб-панель, health, бэкапы, миграция
на PostgreSQL.

**«Личные дела»** — напоминания: `/remindme`, `/remind`, `/remind_cancel`,
`/remind_clear` (таблица `reminders`, фоновая доставка, переживает рестарт).

## 6. Удалено по запросу: WARDOGS

По отдельному решению убран игровой сервер WARDOGS — команда `/wardogs`,
живой статус и страница подключения:

- удалён ког `app/cogs/wardogs/`, сервис `app/services/wardogs_service.py`
  (клиент `api.wardogservers.com`) и `tests/test_wardogs_service.py`;
- удалены 3 поля `Config` (`wardogs_server_name`, `wardogs_server_id`,
  `wardogs_join_url`), константа `_DEFAULT_WARDOGS_SERVER_ID` с Join ID и
  ключи `WARDOGS_*` из `.env.example`;
- из панели удалены страница `/wardogs/join`, эндпоинт
  `/api/wardogs/join-link`, 4 метода (`_wardogs_service`,
  `_api_wardogs_join_link`, `_wardogs_join_page`, `_wardogs_page_html`) и
  ставший неиспользуемым `import html`;
- из `/help` убран мёртвый `reactrole` — команда reaction-ролей удалена
  раньше, но осталась в списке категории «Сообщество».

Ког подхватывается автозагрузчиком по обходу пакета, поэтому отдельная правка
`COG_PROVIDERS` не потребовалась. Итог: 27 когов, 51 команда, 62 эндпоинта
панели.

## 7. Удалено по запросу: приветствия

Убран вступительный/прощальный блок целиком — ког, настройки и слой данных:

- удалён `app/cogs/administration/greetings.py` (публичный пост о входе, пост
  об уходе, каталог каналов в ЛС);
- из `/setup` удалены команды `welcome-channel` и `farewell-channel`, варианты
  `welcome`/`farewell` в `/setup unset` и два поля в `/setup show`;
- из `Config` удалены 12 полей `welcome_*` и ключи `WELCOME_*` из
  `.env.example` (вместе с дефолтом «приходи на стримы» — стримов в копии нет);
- из схемы `guild_settings` удалены колонки `welcome_channel_id` и
  `farewell_channel_id` (SQLite и PostgreSQL), из `DEFAULT_SETTINGS` и
  `_INT_COLUMNS` в `settings_repository.py` — из белого списка записи;
- из веб-панели удалены блок настроек «👋 Приветствия», колонки в
  `_SETTING_COLUMNS` и снапшот `welcome` в `_modules_status`;
- `tests/test_data.py` переведён на `log_channel_id`.

Попутно в панели обнаружены и удалены хвосты от прошлых чисток, которые не
попали под предыдущую проверку: секция «🤖 AI-чат (Gemini)» в `index.html`,
функции `loadAI`/`toggleAIPause` и пункт навигации `AI-чат` в `panel.js`,
группа настроек «💸 Донаты» (`donation_channel_id`) и подписи удалённых модулей
в `MODULE_LABELS` (`role_menu`, `donations`, `overlay`, `kick`, `twitch`,
`ai_chat`, `seasons`).

Итог: 26 когов, 51 команда, 76 полей `Config`.

## 8. Проверка

| Проверка | Результат |
| --- | --- |
| `pytest -q` | 76 passed |
| `ruff check app main.py scripts tests` | All checks passed |
| `python scripts/preflight.py` | успешно (Python 3.12.10, зависимости, токен, `.env`) |
| Импорт всех когов | 26 когов загружаются, ошибок нет |
| Slash-команды | 51 команда, ни медийных, ни `/wardogs` |
| Таблицы SQLite | 17 таблиц, медийных нет |
| Веб-панель | поднимается, 62/62 роута согласованы; навигация 18/18 без дублей |
| Graceful shutdown | БД закрывается корректно |

Живой прогон с Discord-токеном не выполнялся: для этого нужен реальный
`BOT_TOKEN` (в копии его нет — файл `.env` не переносился).

## 9. Что осталось на заметку

- Внутреннее имя класса — `ClanBot` (`app/core/bot.py`). Функционально всё
  верно, но для публичного релиза лучше переименовать в `ClanBot`.
- Панель по-прежнему доверяет `PANEL_PASSWORD` из `.env`; для production
  лучше использовать `PANEL_PASSWORD_HASH` (Argon2) — поддержка есть.
- `BOT_PREFIX` в `.env` не используется: все команды slash. Параметр
  оставлен, потому что discord.py требует его при создании бота.

## 10. Перенос: ивенты (сборы) и «Где играем»

Источники: VacationBot (приватный репозиторий) и eda-discord-bot
(pp/where_play.py). Обе функции переписаны под текущую архитектуру
(ког → сервис → репозиторий → БД), без обращений к БД из когов.

### 10.1 Ивенты (сборы)

| Что | Файл |
| --- | --- |
| Ког и команды | pp/cogs/events/events.py |
| Бизнес-логика, мастер, валидация | pp/services/event_service.py |
| CRUD и отметки участия | pp/db/events_repository.py |
| Устойчивый селект участия | pp/core/views.py (EventSignupView) |
| Таблицы | events, event_signup (миграция 9) |

- Команды: /event, /event_edit, /event_cancel, /event_list, /event_signup.
- Мастер: 7 шагов в личных сообщениях, отменяется словом отмена.
- Роли участия: Есть инфра, Есть тех, Может быть, Сл, Камера, Не иду;
  повторный выбор снимает отметку, «Не иду» не попадает в напоминания.
- Время вводится по Москве (ДД.ММ.ГГГГ ЧЧ:ММ), хранится в ISO 8601 UTC.
- Напоминания за EVENTS_REMINDER_LEAD_MINUTES до сбора и начала, с флагами
  в БД — переживают перезапуск.
- ID ивента берётся из БД, а не парсится из footer эмбеда; проверяется порядок
  «сбор раньше начала».

### 10.2 Где играем

| Что | Файл |
| --- | --- |
| Ког и команды | pp/cogs/events/where_play.py |
| HTTP, кэш снапшота, карточка | pp/services/where_play_service.py |
| CRUD карточки и комнат | pp/db/where_play_repository.py |
| Кнопка «Я коллер» | pp/core/views.py (WherePlayCallerView) |
| Таблицы | where_play, caller_rooms (миграция 9) |

- Команды: /setup_where_play, /where_play, /stop_play,
  /where_play_status, /where_play_card.
- HTTP идёт через общий ApiClient (таймауты, retry, circuit breaker) с ETag
  и 304 Not Modified.
- Код подключения валидируется: только число или UUID community-сервера,
  длина в пределах JOIN_CODE_MIN/JOIN_CODE_MAX; название сервера отвергается.
- Значения из внешнего API экранируются и обрезаются, @ тоже экранируется,
  чтобы удалённый сервер не разослал пинг от имени бота.
- Устаревшие или недоступные данные помечаются предупреждением в карточке,
  а не приводят к потере предыдущих значений.
- Карточка и очередь коллеров хранятся в БД и переживают рестарт.
- Права: администратор или роль коллеров из /setup_where_play.

### 10.3 Конфигурация

EVENTS_REMINDER_LEAD_MINUTES, EVENTS_CHECK_INTERVAL_SECONDS,
EVENTS_MAX_ACTIVE_PER_GUILD, WHERE_PLAY_API_URL, WHERE_PLAY_POLL_SECONDS,
JOIN_CODE_MIN, JOIN_CODE_MAX — все с разумными значениями по умолчанию,
описаны в `.env.example`.

### 10.4 Исправленные ошибки переноса

- `parse_event_datetime` вычитал московское время, а прибавлял 3 часа к
  введённому значению: 19:00 МСК превращалось в 22:00 МСК.
- `clean_image_url` отвергал пустую строку, из-за чего нельзя было пропустить
  изображение в мастере (оно хранится как пустая строка).
- Публикация ивента шла в личные сообщения вместо канала, из которого мастер
  был запущен.
- Кнопки брошенного мастра оставались активными и могли опубликовать его данные
  после запуска нового; токены старого потока теперь инвалидируются.
- Селект активности в выборе коллера не имел обработчика.
- `event_signup` в PostgreSQL не ссылался на `events(id)`.

## 11. Перенос: личные дела (анкета в клан)

Источник: `beda-discord-bot/app/dossiers.py` (582 строки). Переписано под текущую
архитектуру (ког → сервис → репозиторий → БД).

| Что | Файл |
| --- | --- |
| Ког и команды | `app/cogs/administration/dossiers.py` |
| Бизнес-логика, валидация, карточки | `app/services/dossier_service.py` |
| CRUD | `app/db/dossiers_repository.py` |
| Кнопки в тикете | там же (AdmissionView, ApprovalView, custom_id `beda:*`) |
| Таблицы | dossier_settings, admissions, dossier_drafts, dossiers (миграция 10) |
| Утилиты ввода | `app/utils/text.py` |

- Команды: /setup_dossiers, /approve_interview, /review_dossier.
- Анкета: ник, имя, город, Steam ID и две разные специализации из шести.
- Публикация создаёт тред в форуме, вешает картинку специализации и выдаёт роли;
  при сбое создания треда кнопка остаётся активной и повтор продолжает публикацию.
- Черновик хранит ревизию: устаревшая кнопка не перезапишет новую анкету.
- Допуск администратора живёт в `admissions` и гаснет вместе с закрытием тикета.

### 11.1 Отличия от оригинала

- В этом проекте тикет не собирает форму заявки, поэтому город и Steam ID
  спрашиваются в самой модалке анкеты. Пустые значения администратор уточняет
  в `/review_dossier` — оттуда иначе взяться им негде.
- `CooldownGuard` (таблица в БД) заменён на in-memory sliding window, аудит —
  на `logger`: отдельной таблицы аудита досье в проекте нет.
- `ticket_applications` не переносилась: источника данных в новой схеме нет.

## 12. Панель заявок в клан

Текст панели (`ЗАЯВКА В КЛАН [BEDA]`) вынесен в `app/core/ticket_content.py` и
стал дефолтом для `ticket_panel_title`, `ticket_panel_description`,
`ticket_panel_footer`, `ticket_open_label` и `ticket_open_emoji`.

`SettingsRepository.ensure_row` теперь вставляет значения `DEFAULT_SETTINGS`
явно, а не полагается на `DEFAULT` из `CREATE TABLE`. Раньше текст из Python
никогда не доходил до новых серверов: строка создавалась пустой, СУБД подставляла
свои значения, а `DEFAULT_SETTINGS` применялся только к `NULL`.

## 13. Исправленная синхронизация команд

`_sync_commands` синхронизировал и глобальный список, и список гильдии. Гильдейный
`CommandTree.sync(guild=...)` берёт только команды, зарегистрированные именно для
этой гильдии (`CommandTree._get_all_commands`), а проект регистрирует всё глобально.
Второй вызов уходил с пустым списком и **стирал все команды сервера при каждом
запуске**. Теперь синк только глобальный — команды и так наследуются всеми
серверами, где есть бот.

Проверено на живом сервере: глобальных команд 64, гильдейских 0, чужие команды
(`create_event`, `publish_caller`, `publish_rules`, `setup_tickets`,
`setup_voice_rooms`) удалены.

## 14. Переименование в BEDA Clan Bot и мелкие исправления

- MegaBot -> ClanBot, MegaCog -> ClanCog (весь код, тесты, документация).
- Убран бренд «Асуна Юки»: embeds.BOT_NAME теперь BEDA, подписи в когах
  берут имя из общей константы.
- Метрики веб-панели переименованы megabot_* -> clanbot_*.
- main.py: preflight перед подключением печатает имя бота, application_id и
  guild_id из токена, а PrivilegedIntentsRequired завершает процесс кодом 1
  с понятной инструкцией вместо голого трейсбека.
- where_play: ошибка неверного кода подключения теперь показывает, что
  именно ввёл пользователь, и объясняет, где взять код (было: «нужно число или
  UUID» без подсказки).
- Бэкапы: DatabaseBackupManager больше не считает бота SQLite-only. Добавлены
  Database.is_postgres и Database.backend, для PostgreSQL файлы называются
  ot-*.dump, retention чистит оба типа. Отсутствующий pg_dump теперь
  пишет одну понятную строку с инструкцией pt-get install postgresql-client
  и попадает в /health, а не сыпет трейсбек каждый час.
- Убраны дубли команд: /gstart (подмножество /giveaway) и /greroll
  (копия /reroll).
