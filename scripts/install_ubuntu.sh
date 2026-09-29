#!/usr/bin/env bash
# Установка бота на Ubuntu/Debian. Запускать от root или через sudo.
set -euo pipefail

APP_DIR="/opt/beda-clan-bot"
SERVICE_NAME="beda-clan-bot"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Системные пакеты (python, opus, git)"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    libopus0 opus-tools \
    git curl

echo "==> Пользователь discord"
if ! id -u discord >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin discord
fi

echo "==> Копирование проекта"
mkdir -p "$APP_DIR"
if [ ! -f "$APP_DIR/main.py" ]; then
    cp -r "$REPO_DIR/app" "$APP_DIR/"
    cp -r "$REPO_DIR/systemd" "$APP_DIR/"
    cp -r "$REPO_DIR/scripts" "$APP_DIR/"
    cp -r "$REPO_DIR/tests" "$APP_DIR/"
    cp "$REPO_DIR/main.py" "$REPO_DIR/requirements.txt" "$REPO_DIR/requirements-dev.txt" "$REPO_DIR/pyproject.toml" "$REPO_DIR/.env.example" "$APP_DIR/"
    chown -R discord:discord "$APP_DIR"
else
    echo "    проект уже на месте, пропускаю копирование"
fi

echo "==> Виртуальное окружение"
cd "$APP_DIR"
if [ ! -d ".venv/bin" ]; then
    su -s /bin/bash discord -c "python3 -m venv $APP_DIR/.venv"
fi
su -s /bin/bash discord -c "$APP_DIR/.venv/bin/pip install --upgrade pip --quiet"
su -s /bin/bash discord -c "$APP_DIR/.venv/bin/pip install -r $APP_DIR/requirements.txt --quiet"

echo "==> .env"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "!! Впишите токен в $APP_DIR/.env (BOT_TOKEN=...)"
else
    echo "    .env уже существует"
fi

echo "==> systemd-сервис"
cp "$APP_DIR/systemd/$SERVICE_NAME.service" /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME >/dev/null 2>&1

echo "==> Готово"
echo ""
echo "Запуск:   systemctl start $SERVICE_NAME"
echo "Статус:   systemctl status $SERVICE_NAME"
echo "Логи:     journalctl -u $SERVICE_NAME -f"
echo "Файл БД:  $APP_DIR/data/bot.db"
echo ""
echo "Не забудьте выдать боту необходимые права на сервере Discord."