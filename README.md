# builder

## Local setup

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 14+ running on `localhost:5432`

```sh
# macOS
brew install postgresql@16 && brew services start postgresql@16

# Debian/Ubuntu
sudo apt install postgresql && sudo systemctl start postgresql
```

### 2. Environment file

```sh
cp .env.example .env
```

Then fill in the values. `.env` is gitignored and must never be committed.

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | Deployment mode; `development` locally. |
| `DATABASE_URL` | Owner connection, used for migrations and schema changes. |
| `APP_DATABASE_URL` | Least-privilege runtime role (`builder_app`) — DML only, no DDL. |
| `BASE_URL` | Public origin the app builds links and redirects against. |
| `DEV_AUTH_SECRET` | Dev-only shared secret for the local auth bypass. Never set outside development. |
| `JWT_PRIVATE_KEY` | Ed25519 key that signs tokens now. |
| `JWT_PRIVATE_KEY_NEXT` | Standby key for rotation — published for verification before it starts signing. |

Generating fresh secrets:

```sh
openssl rand -base64 24 | tr '+/' '-_'   # DEV_AUTH_SECRET
openssl genpkey -algorithm ed25519       # JWT_PRIVATE_KEY / _NEXT
```

### 3. Database

```sh
./scripts/setup-local.sh
```

The script is idempotent. It reads `.env`, creates the database and both roles
if missing, grants `builder_app` read/write on current and future tables in
`public` (but not DDL), and verifies that both connection strings connect.

### 4. Verify

```sh
psql "$(sed -n 's/^DATABASE_URL=//p' .env | sed 's|+psycopg||')" -c '\conninfo'
```

## Troubleshooting

**`could not connect to Postgres as a superuser to bootstrap`** — the server is
up but no role the script tried can log in. Create your own role once:

```sh
sudo -u postgres createuser --superuser "$(whoami)"
```

**`FAILED: APP_DATABASE_URL could not connect`** — `builder_app` has no
password, so `pg_hba.conf` must allow `trust` for local connections. Homebrew
and most distro packages default to this. After editing `pg_hba.conf`, reload:

```sh
psql -d postgres -c 'SELECT pg_reload_conf();'
```
