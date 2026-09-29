# Infrastructure Layer

Этот модуль предоставляет критически важные компоненты для надёжной работы бота:
проверки здоровья, защиту от каскадных сбоев внешних API, защиту от лимитов
Discord и graceful degradation при недоступной БД.

## Развёртывание

### systemd

```bash
sudo cp systemd/beda-clan-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now beda-clan-bot
journalctl -u beda-clan-bot -f
```

Unit запускает `main.py` из `/opt/beda-clan-bot`, читает `.env` и
автоматически перезапускает бота при падении.

### Docker

```bash
docker compose up -d
docker compose logs -f bot
```

Compose поднимает бота и (по профилю `postgres`) PostgreSQL. Веб-панель внутри
сети, наружу не публикуется — доступ только через SSH-туннель или VPN.

### Бэкапы

SQLite-копии создаются автоматически (`DB_BACKUP_DIR`, `DB_BACKUP_INTERVAL_HOURS`,
`DB_BACKUP_RETENTION`) с консистентной транзакцией. Ручной экспорт — в панели
(`/api/backup/db`) или через миграцию на PostgreSQL.

## Компоненты

### 1. Health Checker (`app/core/health.py`)

Мониторинг здоровья всех сервисов бота.

**Возможности:**
- Проверка Discord API (latency, connection status)
- Проверка БД (SQLite integrity)
- Проверка голосовых подключений (число активных соединений)
- Проверка памяти (RSS, tracemalloc)
- Проверка rate limiter и circuit breakers

**Использование:**
```python
from app.core.health import HealthChecker

checker = HealthChecker(bot)
results = await checker.check_all()
summary = checker.get_summary(results)
```

**Команды:**
- `/health` — полная диагностика всех сервисов
- `/status` — краткий статус бота
- `/ping` — проверка задержки

### 2. Circuit Breaker (`app/core/circuit_breaker.py`)

Защита от каскадных сбоев внешних API.

**Состояния:**
- **CLOSED** — нормальная работа, запросы проходят
- **OPEN** — сбои, все запросы отклоняются
- **HALF_OPEN** — проверка восстановления

**Использование:**
```python
from app.core.circuit_breaker import get_circuit_breaker

circuit = get_circuit_breaker("external_api")

try:
    result = await circuit.call(api_call())
except CircuitOpenError:
    # Fallback logic
    pass
```

**Автоматическое восстановление:**
- После `recovery_timeout` (60s по умолчанию)
- Переходит в HALF_OPEN для тестового запроса
- При успехе → CLOSED
- При ошибке → обратно OPEN

### 3. Rate Limiter (`app/core/rate_limiter.py`)

Защита от превышения лимитов Discord API.

**Алгоритм:**
- Token bucket для каждого route
- Глобальный bucket для всех запросов
- Учёт `Retry-After` заголовков
- Автоматическое ожидание при достижении лимита

**Использование:**
```python
from app.core.rate_limiter import get_rate_limiter

limiter = get_rate_limiter()

# Wait if necessary
wait_time = await limiter.acquire("/channels/123/messages")

# Make API call
await channel.send("message")
```

**Лимиты:**
- Глобальный: 50 req/s
- Messages: 5 req/s
- Message delete: 5 req/s
- Members: 5 req/s
- По умолчанию: 10 req/s

### 4. Base Service (`app/core/base_service.py`)

Базовый класс для сервисов с graceful degradation.

**Возможности:**
- Автоматическое переключение на кеш при ошибке БД
- Автоматическое восстановление при успехе
- TTL для кешированных значений
- Логирование всех переключений

**Использование:**
```python
from app.core.base_service import BaseService

class MyService(BaseService):
    async def get_data(self, guild_id: int):
        return await self._safe_db_call(
            self._repo.get_data(guild_id),
            cache_key=f"data_{guild_id}",
            cache_ttl=300.0,
            fallback={},
        )
```

## Интеграция

### В API Client

```python
# Автоматический circuit breaker для внешних API
status, data, headers = await client.json_with_circuit(
    "GET",
    "https://api.example.com/status",
    circuit_name="external_api",
)
```

### В сервисах

```python
# Graceful degradation при потере БД
settings = await settings_service.get(guild_id)  # Вернёт кеш если БД недоступна
```

### В когах

```python
# Health check в командах
from app.core.health import HealthChecker

checker = HealthChecker(self.bot)
results = await checker.check_all()
```

## Мониторинг

### Логи

Все компоненты логируют важные события:

```log
INFO  bot.circuit: external_api: circuit OPEN (threshold reached: 5 failures)
INFO  bot.circuit: external_api: circuit HALF_OPEN (recovery attempt)
INFO  bot.circuit: external_api: circuit CLOSED (recovered)
WARN  bot.ratelimit: Rate limiter: global retry-after for 5.00s
ERROR bot.services: SettingsService: DB unavailable after 3 errors: database is locked
```

### Статистика

```python
# Circuit breaker stats
from app.core.circuit_breaker import get_circuit_breaker_registry

stats = get_circuit_breaker_registry().get_all_stats()
# {
#   "external_api": {"state": "closed", "total_calls": 150, "failed_calls": 2},
# }

# Rate limiter stats
from app.core.rate_limiter import get_rate_limiter

stats = get_rate_limiter().get_stats()
# {"global_tokens": 45, "active_buckets": 3, "retry_after_active": False}
```

## Конфигурация

Параметры настраиваются в коде при создании компонентов:

```python
# Circuit breaker
circuit = CircuitBreaker(
    "service_name",
    failure_threshold=5,      # Сбоев до открытия
    recovery_timeout=60.0,    # Секунд до попытки восстановления
    half_open_max_calls=3,    # Тестовых запросов в HALF_OPEN
)

# Rate limiter (глобальный, singleton)
limiter = DiscordRateLimiter()
limiter.ROUTE_LIMITS["messages"] = (5, 1.0)  # 5 req/s
```

## Best Practices

1. **Всегда используйте circuit breaker для внешних API**
   ```python
   result = await circuit.call(api_call(), fallback=default_value)
   ```

2. **Используйте graceful degradation в сервисах**
   ```python
   data = await self._safe_db_call(db_call(), cache_key="key")
   ```

3. **Проверяйте здоровье сервисов регулярно**
   ```python
   # В мониторинге или алертинге
   health = await checker.check_all()
   ```

4. **Логируйте все ошибки и восстановления**
   - Circuit breaker делает это автоматически
   - Base service логирует переключения на кеш

5. **Мониторьте статистику**
   - Circuit breaker: failed_calls, state
   - Rate limiter: retry_after_active
   - Services: db_available, error_count
