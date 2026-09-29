FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# libopus — обязателен для голосовых каналов (py-nacl)
# curl + ca-certificates — healthcheck и служебные утилиты
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 libopus-dev curl ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*

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

EXPOSE 3000

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["python", "main.py"]
