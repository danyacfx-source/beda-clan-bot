#!/usr/bin/env bash
# Загружает SQL-дамп (Adminer / pg_dump, plain SQL) в базу бота.
#
#   scripts/pg_import_dump.sh [--force [--yes]] [--env путь/к/.env] beda_dump.sql
#
# DATABASE_URL берётся из строки DATABASE_URL= файла --env (или ./.env проекта);
# файл не выполняется через source. Переменная окружения DATABASE_URL
# используется только если в файле строки нет (в общем контейнере она может
# принадлежать другому боту).
# Без --force скрипт отказывается работать, если в схеме public уже есть таблицы.
# С --force схема public пересоздаётся (ВСЕ данные в ней удаляются) после ввода
# имени базы с клавиатуры; --yes пропускает вопрос только без терминала.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
FORCE=0
ASSUME_YES=0
DUMP=""

usage() {
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        --env) ENV_FILE="${2:?--env требует значение}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "Неизвестный аргумент: $1" >&2; usage >&2; exit 2 ;;
        *) DUMP="$1"; shift ;;
    esac
done

if [ "$ASSUME_YES" -eq 1 ] && [ "$FORCE" -ne 1 ]; then
    echo "--yes имеет смысл только вместе с --force." >&2
    exit 2
fi
if [ -z "$DUMP" ]; then
    usage >&2
    exit 2
fi
if [ ! -f "$DUMP" ]; then
    echo "Файл дампа не найден: $DUMP" >&2
    exit 1
fi
command -v psql >/dev/null 2>&1 || { echo "psql не найден (apt install postgresql-client)." >&2; exit 1; }

mask_url() {
    sed -E \
        -e 's#(://[^:/@?\#]*):[^?\#]*@#\1:***@#' \
        -e 's#([?&](password|sslpassword|passfile)=)[^&[:space:]\#]*#\1***#Ig'
}

read_env_url() {
    local file="$1" line value
    [ -f "$file" ] || return 0
    line="$(grep -E '^[[:space:]]*DATABASE_URL[[:space:]]*=' "$file" | tail -n 1 || true)"
    [ -n "$line" ] || return 0
    value="${line#*=}"
    value="${value%$'\r'}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"*\" && ${#value} -ge 2 ]] || [[ "$value" == \'*\' && ${#value} -ge 2 ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

ENV_VALUE="${DATABASE_URL:-}"
unset DATABASE_URL
DATABASE_URL="$(read_env_url "$ENV_FILE")"
if [ -n "$DATABASE_URL" ]; then
    if [ -n "$ENV_VALUE" ] && [ "$ENV_VALUE" != "$DATABASE_URL" ]; then
        echo "[WARN] переменная окружения DATABASE_URL ($(printf '%s' "$ENV_VALUE" | mask_url)) отличается от файла и игнорируется;" >&2
        echo "       используется значение из $ENV_FILE" >&2
    fi
elif [ -n "$ENV_VALUE" ]; then
    echo "[WARN] в $ENV_FILE нет DATABASE_URL, беру переменную окружения" >&2
    DATABASE_URL="$ENV_VALUE"
fi
if [ -z "$DATABASE_URL" ]; then
    echo "DATABASE_URL не найден ни в $ENV_FILE, ни в окружении" >&2
    exit 1
fi
case "$DATABASE_URL" in
    postgresql://*|postgres://*) ;;
    *) echo "DATABASE_URL должен начинаться с postgresql://" >&2; exit 1 ;;
esac

masked="$(printf '%s' "$DATABASE_URL" | mask_url)"
echo "Целевая БД: $masked"

export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"
PSQL=(psql -X -q -v ON_ERROR_STOP=1 "$DATABASE_URL")

if ! "${PSQL[@]}" -tAc "SELECT 1" >/dev/null 2>&1; then
    echo "Не удалось подключиться к БД по DATABASE_URL." >&2
    exit 1
fi

dbname="$("${PSQL[@]}" -tAc "SELECT current_database()")"
tables="$("${PSQL[@]}" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")"
if [ "$tables" -gt 0 ] && [ "$FORCE" -ne 1 ]; then
    echo "В схеме public уже есть таблиц: $tables. Импорт отменён." >&2
    echo "Если бот уже запускался на этой БД и данные не нужны — повторите с --force (схема public будет пересоздана)." >&2
    exit 1
fi

if [ "$FORCE" -eq 1 ]; then
    echo "ВНИМАНИЕ: --force удалит схему public в базе '$dbname' ($masked), таблиц: $tables." >&2
    if [ -t 0 ] || { : < /dev/tty; } 2>/dev/null; then
        read -r -p "Введите имя базы '$dbname' для подтверждения: " answer < /dev/tty
        if [ "$answer" != "$dbname" ]; then
            echo "Имя не совпало, отмена." >&2
            exit 1
        fi
    elif [ "$ASSUME_YES" -ne 1 ]; then
        echo "Нет терминала для подтверждения; для неинтерактивного запуска добавьте --yes." >&2
        exit 1
    fi
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
: > "$WORKDIR/prefix.sql"
if [ "$tables" -gt 0 ]; then
    echo "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" > "$WORKDIR/prefix.sql"
fi
# Выравнивание последовательностей — в той же транзакции, что и загрузка.
cat > "$WORKDIR/fix.sql" <<'SQL'
SET search_path = public, pg_catalog;
DO $$
DECLARE
    r record;
    seq text;
BEGIN
    FOR r IN
        SELECT table_schema, table_name, column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_default LIKE 'nextval(%'
    LOOP
        seq := substring(r.column_default from 'nextval\(''(.*)''::regclass\)');
        IF seq IS NULL THEN
            RAISE NOTICE 'пропуск %.%: не разобран default %', r.table_name, r.column_name, r.column_default;
            CONTINUE;
        END IF;
        EXECUTE format(
            'SELECT setval(%L, GREATEST(COALESCE(max(%I), 1), 1), max(%I) IS NOT NULL) FROM %I.%I',
            seq, r.column_name, r.column_name, r.table_schema, r.table_name
        );
    END LOOP;
END $$;
SQL

echo "Загружаю $DUMP и выравниваю последовательности (одна транзакция) ..."
if ! psql -X -q -v ON_ERROR_STOP=1 --single-transaction "$DATABASE_URL" \
    -f "$WORKDIR/prefix.sql" -f "$DUMP" -f "$WORKDIR/fix.sql" >/dev/null; then
    echo "Импорт не удался; транзакция откачена, база не изменена." >&2
    exit 1
fi
echo "[OK] дамп загружен, последовательности выровнены"

echo "Строк в основных таблицах:"
for table in tickets vacations events dossiers guild_settings; do
    exists="$("${PSQL[@]}" -tAc "SELECT to_regclass('public.\"$table\"') IS NOT NULL")"
    if [ "$exists" = "t" ]; then
        count="$("${PSQL[@]}" -tAc "SELECT count(*) FROM public.\"$table\"")"
        printf '  %-16s %s\n' "$table" "$count"
    else
        printf '  %-16s %s\n' "$table" "(таблицы нет в дампе)"
    fi
done
echo "Готово. Теперь можно запускать бота: scripts/run.sh --background"
