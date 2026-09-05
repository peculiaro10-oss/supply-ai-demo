# Cauldra production launch

The application is designed to be served as one HTTPS site: the browser receives
`frontend/index.html`, `/assets`, `/css`, `/js`, and the API from the same public
domain. This avoids a cross-origin configuration and keeps refresh cookies
first-party.

## Repository layout

```
backend/     FastAPI server — main.py, storage.py, upcitemdb_provider.py, sms_service.py
frontend/    the web client — index.html, css/, js/, assets/, sw.js
alembic/     database migrations (run from the repo root; alembic.ini adds backend/ to the path)
scripts/     one-off operational scripts
tests/       pytest suite (root conftest.py puts backend/ on sys.path)
```

`backend/main.py` resolves the frontend bundle relative to the repo root
(`PROJECT_ROOT / "frontend"`), and serves `index.html` at `/`, `sw.js` at
`/sw.js`, and mounts `assets/`, `css/`, `js/` at `/assets`, `/css`, `/js`.
Uvicorn is started with `--app-dir backend` so a bare `import main` still
resolves (see the `Dockerfile` CMD and `.claude/launch.json`).

## Before deploying

1. Rotate every credential that has ever been placed in a shared file, browser,
   chat, screenshot, or source-control history. In particular, create a fresh
   `SUPPLY_AI_SECRET_KEY` and replace any API, SMS, or email provider keys.
2. Copy `.env.production.example` to `.env` **on the production host**. Set
   `SUPPLY_AI_TRUSTED_HOSTS` to the exact public host name. Do not use `*`.
3. PostgreSQL is required in every environment — development, staging, and
   production. There is no SQLite fallback: `main.py` raises a clear
   `RuntimeError` at startup if `DATABASE_URL` is missing or not a
   PostgreSQL URL. This application runs on **Supabase PostgreSQL** in
   production. Set `DATABASE_URL` to your Supabase project's **Session
   Pooler** connection string (Project Settings → Database → Connection
   string → "Session pooler") — not the direct connection or the transaction
   pooler. The session pooler is IPv4-reachable and supports the prepared
   statements this app relies on; the transaction pooler (port 6543) does
   not, and the direct connection is IPv6-only unless the project has the
   IPv4 add-on. A bare `postgresql://` or Heroku-style `postgres://` URL is
   automatically normalized to the explicit `postgresql+psycopg://` scheme
   (matching the `psycopg[binary]` driver pinned in `requirements.txt`) so
   the driver actually used is never left to chance.
4. Choose the schema-management path that owns the target database. For the
   manually installed Supabase schema, run `verify_supabase_schema.sql` in the
   SQL Editor and require its PASS notice; do **not** also run Alembic against
   that same project. For a separate environment explicitly owned by Alembic,
   run this one-time release step before starting or scaling containers:

   ```sh
   docker run --rm --env-file .env cauldra alembic upgrade head
   ```

   The application container only starts Uvicorn; it never runs migrations at
   instance startup. This prevents multiple replicas from attempting the same
   production migration concurrently.
5. Set conservative connection-pool variables (`DATABASE_POOL_SIZE=5`,
   `DATABASE_MAX_OVERFLOW=10`, `DATABASE_POOL_TIMEOUT=30`,
   `DATABASE_POOL_RECYCLE=1800`, and `DATABASE_CONNECT_TIMEOUT=10`) unless
   measured database capacity supports more — `create_engine()` in `main.py`
   reads all five directly, with these same values as its defaults if unset.
   `DATABASE_POOL_RECYCLE` matters especially with a pooler in front of
   Postgres, to avoid stale connections being reused after Supabase closes
   them idle. Remember total connections against the database are
   approximately `workers x (pool_size + max_overflow)` — check the
   provider's actual connection limit before raising these for multiple
   workers/replicas. `DATABASE_SSL_MODE` is applied only when `DATABASE_URL`
   doesn't already carry its own `sslmode=` query parameter.
6. Keep object storage private. For Supabase deployments, create a private
   `cauldra-private` bucket, set `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (or the
   legacy `SUPABASE_SERVICE_ROLE_KEY`), `SUPABASE_STORAGE_BUCKET`, and
   `SUPPLY_AI_STORAGE_BACKEND=supabase`. Local `/data/uploads` remains suitable
   only for offline/single-instance development. Never expose the server key in
   the frontend or make retained business documents public.
7. Terminate TLS at a trusted reverse proxy (for example Caddy, Nginx, or your
   platform's HTTPS service) and forward traffic to port 8000. The public site
   must be HTTPS because production refresh cookies are marked `Secure`.

## Docker launch

From this directory, after creating the production `.env`, configuring the
private Supabase Storage bucket, and verifying the manually installed schema:

```sh
docker build -t cauldra .
docker run --env-file .env -p 127.0.0.1:8000:8000 -v cauldra_data:/data --name cauldra --restart unless-stopped cauldra
```

Point the HTTPS reverse proxy to `http://127.0.0.1:8000`. Do not expose port
8000 directly to the public internet. Configure the reverse proxy to pass the
original `Host` and `X-Forwarded-Proto` headers.

## Post-deploy verification

Open `https://your-domain/health` and confirm it returns status `ok`, then
test registration/login, password change, forgot-password delivery, a file
upload, inventory update, and a logout/login on a separate browser session.

Back up Supabase PostgreSQL before each application update and test a restore
regularly — Supabase provides point-in-time recovery and scheduled backups on
paid plans; confirm the retention window meets your needs, and additionally
keep an independent `pg_dump` off-platform — an automated script for exactly
this (pg_dump -> Cloudflare R2) is in `scripts/backup_database.py`; see
`BACKUP_RESTORE.md` for how to run it and how to restore from it. The historical pre-Supabase SQLite
files and the one-time SQLite→PostgreSQL migration tooling have been moved to
`archive/sqlite-legacy/` (out of the runtime/deploy path); PostgreSQL is now the
only supported engine in every environment.
A launch is complete only after HTTPS, DNS, a real email sender domain, backups,
and a tested restore procedure have been configured by the deployment owner.
