#!/usr/bin/env sh
# Точка входа контейнера: приводит права на bind-mount'ы и запускает бота
# от непривилегированного пользователя.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    # Bind-mounted ./data and ./logs are commonly created by root on the host.
    # Fix their ownership once, then run the actual bot as an unprivileged user.
    chown -R bot:bot /app/data /app/logs 2>/dev/null || true
    exec gosu bot "$0" "$@"
fi

umask 077

exec "$@"
