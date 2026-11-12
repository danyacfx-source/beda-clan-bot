#!/usr/bin/env bash
# Запуск бота из SSH-консоли без systemd.
#
#   scripts/run.sh               запуск на переднем плане (Ctrl+C — остановка)
#   scripts/run.sh --background  запуск в фоне (nohup + setsid), PID в data/bot.pid
#
# Что делает: создаёт .venv, ставит зависимости при изменении requirements.txt,
# выполняет scripts/preflight.py --strict и запускает main.py в цикле
# автоперезапуска (5 с после падения; код 0 или SIGINT/SIGTERM — остановка).
# Вывод пишется в logs/bot.log. Остановка фонового бота: scripts/stop.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/run.sh"
PIDFILE="$ROOT/data/bot.pid"
LOGDIR="$ROOT/logs"
LOGFILE="$LOGDIR/bot.log"
RESTART_DELAY=5
BACKGROUND=0

for arg in "$@"; do
    case "$arg" in
        --background) BACKGROUND=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Неизвестный аргумент: $arg" >&2; exit 2 ;;
    esac
done

cd "$ROOT" || exit 1
mkdir -p "$ROOT/data" "$LOGDIR"

# Живой ли процесс из pid-файла (и действительно ли это run.sh, а не переиспользованный PID).
pidfile_alive() {
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    if [ -r "/proc/$pid/cmdline" ] && ! tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q "run.sh"; then
        return 1
    fi
    return 0
}

# Замок против гонки двойного старта: flock, а если его нет — mkdir.
LOCK_FILE="$ROOT/data/bot.lock"
LOCK_DIR="$ROOT/data/bot.lock.d"
LOCK_KIND=""
acquire_lock() {
    if command -v flock >/dev/null 2>&1; then
        exec 9> "$LOCK_FILE"
        if ! flock -n 9; then
            echo "Другой запуск бота уже идёт (замок $LOCK_FILE)." >&2
            exit 1
        fi
        LOCK_KIND="flock"
    else
        if ! mkdir "$LOCK_DIR" 2>/dev/null; then
            echo "Другой запуск бота уже идёт (замок $LOCK_DIR; если это не так — удалите каталог)." >&2
            exit 1
        fi
        LOCK_KIND="mkdir"
    fi
}
release_lock() {
    case "$LOCK_KIND" in
        flock) exec 9>&- ;;
        mkdir) rmdir "$LOCK_DIR" 2>/dev/null || true ;;
    esac
    LOCK_KIND=""
}

if [ "$BACKGROUND" -eq 1 ]; then
    acquire_lock
    trap release_lock EXIT
    if pidfile_alive; then
        echo "Бот уже запущен (PID $(cat "$PIDFILE")). Остановите его: scripts/stop.sh" >&2
        exit 1
    fi
    # fd 9 (замок) потомку не передаётся; замок держится, пока pid-файл не появится
    BEDA_RUN_BG=1 nohup setsid bash "$SCRIPT" >> "$LOGFILE" 2>&1 < /dev/null 9>&- &
    sleep 3
    if pidfile_alive; then
        echo "Бот запущен в фоне, PID $(cat "$PIDFILE")."
        echo "Логи:      tail -f logs/bot.log"
        echo "Остановка: scripts/stop.sh"
        exit 0
    fi
    echo "Фоновый запуск не удался. Последние строки лога:" >&2
    tail -n 20 "$LOGFILE" >&2
    exit 1
fi

IN_BG="${BEDA_RUN_BG:-0}"

log() {
    local line
    line="[run.sh $(date '+%Y-%m-%d %H:%M:%S')] $*"
    if [ "$IN_BG" = "1" ]; then
        echo "$line"          # stdout уже перенаправлен в logs/bot.log
    else
        echo "$line" | tee -a "$LOGFILE"
    fi
}

# Запущенный из --background потомок замок не берёт: его держит и проверил родитель.
if [ "$IN_BG" != "1" ]; then
    acquire_lock
fi
if pidfile_alive && [ "$(cat "$PIDFILE")" != "$$" ]; then
    echo "Бот уже запущен (PID $(cat "$PIDFILE")): два экземпляра с одним токеном недопустимы." >&2
    exit 1
fi
echo "$$" > "$PIDFILE"
release_lock

CHILD=""
STOPPING=0
cleanup() {
    if [ "$(cat "$PIDFILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$PIDFILE"
    fi
}
on_signal() {
    STOPPING=1
    if [ -n "$CHILD" ] && kill -0 "$CHILD" 2>/dev/null; then
        kill -TERM "$CHILD" 2>/dev/null || true
    fi
}
trap on_signal INT TERM
trap cleanup EXIT

# Запускает команду в фоне и ждёт её: так сигнал обрабатывается сразу,
# а не после окончания долгого pip/preflight.
run_child() {
    "$@" &
    CHILD=$!
    local rc
    while true; do
        wait "$CHILD"
        rc=$?
        if kill -0 "$CHILD" 2>/dev/null; then
            continue
        fi
        break
    done
    CHILD=""
    return "$rc"
}
stop_if_signalled() {
    if [ "$STOPPING" -eq 1 ]; then
        log "Остановка по сигналу до запуска бота"
        exit 0
    fi
}

# ── окружение ────────────────────────────────────────────────────────────────
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    log "Создаю .venv"
    run_child python3 -m venv "$ROOT/.venv"
    rc=$?
    stop_if_signalled
    if [ "$rc" -ne 0 ]; then
        log "Не удалось создать .venv (нужен пакет python3-venv)"
        exit 1
    fi
fi
PY="$ROOT/.venv/bin/python"

req_hash="$(sha256sum "$ROOT/requirements.txt" | cut -d' ' -f1)"
if [ "$(cat "$ROOT/.venv/.req-hash" 2>/dev/null || true)" != "$req_hash" ]; then
    log "Устанавливаю зависимости из requirements.txt"
    run_child "$PY" -m pip install --quiet --upgrade pip
    rc=$?
    if [ "$rc" -eq 0 ] && [ "$STOPPING" -eq 0 ]; then
        run_child "$PY" -m pip install --quiet -r "$ROOT/requirements.txt"
        rc=$?
    fi
    stop_if_signalled
    if [ "$rc" -eq 0 ]; then
        echo "$req_hash" > "$ROOT/.venv/.req-hash"
    else
        log "pip install завершился с ошибкой"
        exit 1
    fi
fi

run_child "$PY" "$ROOT/scripts/preflight.py" --strict
rc=$?
stop_if_signalled
if [ "$rc" -ne 0 ]; then
    log "Предстартовая проверка не пройдена — бот не запущен"
    exit 1
fi

# ── цикл автоперезапуска ─────────────────────────────────────────────────────
while [ "$STOPPING" -eq 0 ]; do
    log "Запуск main.py"
    if [ "$IN_BG" = "1" ]; then
        "$PY" "$ROOT/main.py" &
    else
        "$PY" "$ROOT/main.py" > >(tee -a "$LOGFILE") 2>&1 &
    fi
    CHILD=$!
    code=0
    while true; do
        wait "$CHILD"
        code=$?
        # wait прерывается сигналом (код > 128): проверяем, жив ли ещё потомок
        if kill -0 "$CHILD" 2>/dev/null; then
            continue
        fi
        break
    done
    CHILD=""
    sleep 0.3  # дать tee дописать хвост лога
    if [ "$STOPPING" -eq 1 ]; then
        log "Остановка по сигналу (код выхода бота $code)"
        break
    fi
    if [ "$code" -eq 0 ]; then
        log "Бот завершился штатно (код 0) — перезапуск не нужен"
        break
    fi
    log "Бот упал с кодом $code, перезапуск через ${RESTART_DELAY} с"
    # sleep в фоне, чтобы сигнал прерывал ожидание сразу
    sleep "$RESTART_DELAY" &
    SLEEPER=$!
    wait "$SLEEPER" 2>/dev/null || true
    kill "$SLEEPER" 2>/dev/null || true
done
exit 0
