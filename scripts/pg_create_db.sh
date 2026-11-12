#!/usr/bin/env bash
# Идемпотентно создаёт отдельную роль и базу бота в уже работающем PostgreSQL.
#
#   scripts/pg_create_db.sh [--db beda] [--user beda] [--reset-password]
#
# Пароль роли: переменная BEDA_DB_PASSWORD или интерактивный ввод (без эха).
# Подключение суперпользователем: PGSUPERUSER (по умолчанию postgres) и
# стандартные PGHOST / PGPORT / PGPASSWORD (по умолчанию — локальный сокет).
set -euo pipefail

DB_NAME="beda"
DB_USER="beda"
RESET_PASSWORD=0

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --db) DB_NAME="${2:?--db требует значение}"; shift 2 ;;
        --user) DB_USER="${2:?--user требует значение}"; shift 2 ;;
        --reset-password) RESET_PASSWORD=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage >&2; exit 2 ;;
    esac
done

NAME_RE='^[a-z_][a-z0-9_]*$'
for value in "$DB_NAME" "$DB_USER"; do
    if ! [[ "$value" =~ $NAME_RE ]]; then
        echo "Недопустимое имя '$value': разрешены a-z, 0-9 и _, первый символ не цифра." >&2
        exit 2
    fi
    if [ "${#value}" -gt 63 ]; then
        echo "Имя '$value' длиннее 63 символов." >&2
        exit 2
    fi
done

command -v psql >/dev/null 2>&1 || { echo "psql не найден (apt install postgresql-client)." >&2; exit 1; }

SUPERUSER="${PGSUPERUSER:-postgres}"
PSQL=(psql -X -q -v ON_ERROR_STOP=1 -U "$SUPERUSER" -d postgres)

if ! "${PSQL[@]}" -tAc "SELECT 1" >/dev/null 2>&1; then
    echo "Не удалось подключиться к PostgreSQL как '$SUPERUSER'." >&2
    echo "Проверьте PGSUPERUSER, PGHOST, PGPORT, PGPASSWORD." >&2
    exit 1
fi

role_exists="$("${PSQL[@]}" -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'")"
db_exists="$("${PSQL[@]}" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")"

need_password=0
if [ -z "$role_exists" ] || [ "$RESET_PASSWORD" -eq 1 ]; then
    need_password=1
fi

PASSWORD="${BEDA_DB_PASSWORD:-}"
if [ "$need_password" -eq 1 ] && [ -z "$PASSWORD" ]; then
    if [ ! -t 0 ]; then
        echo "Задайте пароль через переменную BEDA_DB_PASSWORD (нет терминала для ввода)." >&2
        exit 1
    fi
    read -r -s -p "Пароль для роли $DB_USER: " PASSWORD; echo >&2
    read -r -s -p "Повторите пароль: " PASSWORD2; echo >&2
    if [ "$PASSWORD" != "$PASSWORD2" ]; then
        echo "Пароли не совпадают." >&2
        exit 1
    fi
    unset PASSWORD2
fi
if [ "$need_password" -eq 1 ] && [ -z "$PASSWORD" ]; then
    echo "Пустой пароль не допускается." >&2
    exit 1
fi

# Пароль передаётся psql через stdin (не через аргументы процесса).
ESCAPED_PASSWORD="${PASSWORD//\'/\'\'}"

if [ -z "$role_exists" ]; then
    printf "SET standard_conforming_strings = on;\nCREATE ROLE \"%s\" LOGIN PASSWORD '%s';\n" \
        "$DB_USER" "$ESCAPED_PASSWORD" | "${PSQL[@]}" >/dev/null
    echo "[OK] роль $DB_USER создана" >&2
elif [ "$RESET_PASSWORD" -eq 1 ]; then
    printf "SET standard_conforming_strings = on;\nALTER ROLE \"%s\" LOGIN PASSWORD '%s';\n" \
        "$DB_USER" "$ESCAPED_PASSWORD" | "${PSQL[@]}" >/dev/null
    echo "[OK] пароль роли $DB_USER обновлён" >&2
else
    echo "[OK] роль $DB_USER уже есть (пароль не менялся; --reset-password для смены)" >&2
fi

if [ -z "$db_exists" ]; then
    "${PSQL[@]}" -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\" ENCODING 'UTF8' TEMPLATE template0" >/dev/null 2>&1 \
        || "${PSQL[@]}" -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\"" >/dev/null
    echo "[OK] база $DB_NAME создана (владелец $DB_USER)" >&2
else
    owner="$("${PSQL[@]}" -tAc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$DB_NAME'")"
    if [ "$owner" != "$DB_USER" ]; then
        echo "[WARN] база $DB_NAME уже есть, но её владелец — $owner, а не $DB_USER" >&2
    else
        echo "[OK] база $DB_NAME уже есть" >&2
    fi
fi

# Изоляция от остальных ботов: подключаться могут только владелец и суперпользователь.
"${PSQL[@]}" -c "REVOKE CONNECT ON DATABASE \"$DB_NAME\" FROM PUBLIC" >/dev/null
"${PSQL[@]}" -c "GRANT CONNECT ON DATABASE \"$DB_NAME\" TO \"$DB_USER\"" >/dev/null
echo "[OK] CONNECT для PUBLIC отозван" >&2

HOST="${PGHOST:-127.0.0.1}"
case "$HOST" in /*) HOST="127.0.0.1" ;; esac
PORT="${PGPORT:-5432}"
echo >&2
echo "Добавьте в .env (вместо *** подставьте пароль; спецсимволы в пароле кодируйте как %XX):" >&2
echo "DATABASE_URL=postgresql://${DB_USER}:***@${HOST}:${PORT}/${DB_NAME}"
