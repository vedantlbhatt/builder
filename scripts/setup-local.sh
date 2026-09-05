#!/usr/bin/env bash
# Bootstrap the local Postgres database and roles described by .env.
# Idempotent: safe to re-run.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "error: .env not found. Run: cp .env.example .env" >&2
  exit 1
fi

# Read a KEY=value out of .env without executing it (handles the multi-line
# PEM values by only taking the first line of a match).
env_get() {
  sed -n "s/^$1=//p" .env | head -n 1 | sed 's/^"//; s/"$//'
}

# postgresql+psycopg://user@host/db -> psql-usable postgresql://user@host/db
to_libpq() { sed 's|^postgresql+psycopg://|postgresql://|'; }

DATABASE_URL="$(env_get DATABASE_URL)"
APP_DATABASE_URL="$(env_get APP_DATABASE_URL)"

if [[ -z "$DATABASE_URL" || -z "$APP_DATABASE_URL" ]]; then
  echo "error: DATABASE_URL and APP_DATABASE_URL must be set in .env" >&2
  exit 1
fi

strip_scheme() { sed 's|^[^:]*://||'; }

OWNER="$(printf '%s' "$DATABASE_URL"     | strip_scheme | cut -d@ -f1 | cut -d: -f1)"
DB_NAME="$(printf '%s' "$DATABASE_URL"   | sed 's|.*/||' | cut -d? -f1)"
APP_ROLE="$(printf '%s' "$APP_DATABASE_URL" | strip_scheme | cut -d@ -f1 | cut -d: -f1)"

echo "database : $DB_NAME"
echo "owner    : $OWNER"
echo "app role : $APP_ROLE"

if ! pg_isready -q -d "$(printf '%s' "$DATABASE_URL" | to_libpq)"; then
  echo "error: Postgres is not accepting connections on localhost." >&2
  echo "       macOS (Homebrew): brew services start postgresql@16" >&2
  echo "       Linux:            sudo systemctl start postgresql" >&2
  exit 1
fi

# Pick a superuser connection to the maintenance database. On a fresh cluster
# the owner role may not exist yet, so fall back to the usual bootstrap
# superusers rather than trying to create the owner role as itself.
HOST_PART="$(printf '%s' "$DATABASE_URL" | strip_scheme | sed 's|^[^@]*@||; s|/.*||')"
ADMIN_URL=""
for candidate in "$OWNER" "$(id -un)" postgres; do
  [[ -n "$candidate" ]] || continue
  url="postgresql://${candidate}@${HOST_PART}/postgres"
  if psql -v ON_ERROR_STOP=1 -tAqc "SELECT 1" -d "$url" >/dev/null 2>&1; then
    ADMIN_URL="$url"
    echo "admin    : $candidate"
    break
  fi
done

if [[ -z "$ADMIN_URL" ]]; then
  echo "error: could not connect to Postgres as a superuser to bootstrap." >&2
  echo "       tried roles: $OWNER, $(id -un), postgres" >&2
  exit 1
fi

psql_admin() { psql -v ON_ERROR_STOP=1 -q -d "$ADMIN_URL" "$@"; }

if ! psql_admin -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$OWNER'" | grep -q 1; then
  echo "creating role $OWNER"
  psql_admin -c "CREATE ROLE \"$OWNER\" LOGIN CREATEDB"
fi

if ! psql_admin -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$APP_ROLE'" | grep -q 1; then
  echo "creating role $APP_ROLE"
  psql_admin -c "CREATE ROLE \"$APP_ROLE\" LOGIN"
fi

if ! psql_admin -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
  echo "creating database $DB_NAME"
  psql_admin -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$OWNER\""
fi

# Least-privilege grants for the runtime role: it may use the schema and read
# and write existing and future tables, but not create or drop them.
psql -v ON_ERROR_STOP=1 -q -d "$(printf '%s' "$DATABASE_URL" | to_libpq)" <<SQL
GRANT CONNECT ON DATABASE "$DB_NAME" TO "$APP_ROLE";
GRANT USAGE ON SCHEMA public TO "$APP_ROLE";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "$APP_ROLE";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "$APP_ROLE";
ALTER DEFAULT PRIVILEGES FOR ROLE "$OWNER" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "$APP_ROLE";
ALTER DEFAULT PRIVILEGES FOR ROLE "$OWNER" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO "$APP_ROLE";
SQL

# Verify both connection strings actually work.
for url_name in DATABASE_URL APP_DATABASE_URL; do
  url="$(env_get "$url_name" | to_libpq)"
  if psql -v ON_ERROR_STOP=1 -tAqc "SELECT 1" -d "$url" >/dev/null 2>&1; then
    echo "ok: $url_name connects"
  else
    echo "FAILED: $url_name could not connect" >&2
    echo "  check pg_hba.conf allows passwordless local connections for this role" >&2
    exit 1
  fi
done

echo "local environment ready"
