#!/usr/bin/env bash
# Останавливает бота, запущенного через scripts/run.sh --background.
# SIGTERM, ожидание до 30 с, затем SIGKILL. Удаляет data/bot.pid.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="$ROOT/data/bot.pid"
TIMEOUT=30

if [ ! -f "$PIDFILE" ]; then
    echo "Файл $PIDFILE не найден — бот не запущен через run.sh."
    exit 0
fi

pid="$(cat "$PIDFILE" 2>/dev/null || true)"
if ! [[ "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "Процесс ${pid:-?} не работает, удаляю устаревший pid-файл."
    rm -f "$PIDFILE"
    exit 0
fi
if [ -r "/proc/$pid/cmdline" ] && ! tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q "run.sh"; then
    echo "PID $pid принадлежит другому процессу, не трогаю. Удаляю устаревший pid-файл."
    rm -f "$PIDFILE"
    exit 0
fi

echo "Останавливаю бота (PID $pid), SIGTERM ..."
children="$(pgrep -P "$pid" 2>/dev/null || true)"
kill -TERM "$pid" 2>/dev/null || true

for ((i = 0; i < TIMEOUT; i++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done

if kill -0 "$pid" 2>/dev/null; then
    echo "Не завершился за ${TIMEOUT} с, SIGKILL."
    # shellcheck disable=SC2086
    [ -n "$children" ] && kill -KILL $children 2>/dev/null
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
fi

rm -f "$PIDFILE"
echo "Бот остановлен."
