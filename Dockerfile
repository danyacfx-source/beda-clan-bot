FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# libopus — обязателен для голосовых каналов (py-nacl)
# curl + ca-certificates — healthcheck и служебные утилиты
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 libopus-dev curl ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*

# pg_dump для резервных копий PostgreSQL. Версия клиента совпадает с сервером
# из docker-compose.yml (postgres:16): pg_dump отказывается выгружать сервер
# новее себя, а в Debian по умолчанию другая версия. При смене образа
# postgres обновите PG_CLIENT_VERSION.
ARG PG_CLIENT_VERSION=16
RUN install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-client-${PG_CLIENT_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && pg_dump --version

WORKDIR /app

RUN addgroup --system bot \
    && adduser --system --ingroup bot --home /app --no-create-home bot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN sed -i 's/\r$//' /app/scripts/docker-entrypoint.sh \
    && chmod +x /app/scripts/docker-entrypoint.sh \
    && mkdir -p /app/data /app/logs \
    && chown -R bot:bot /app

# БД, токены и логи должны жить в volume (см. docker-compose.yml / хост)
VOLUME ["/app/data", "/app/logs"]

EXPOSE 8000

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["python", "main.py"]
