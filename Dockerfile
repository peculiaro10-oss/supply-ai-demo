FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# PostgreSQL client tools (pg_dump/pg_restore) — required by
# scripts/backup_database.py for disaster-recovery backups to Cloudflare R2.
# Client tools only, never a PostgreSQL server. Debian's default
# postgresql-client package tracks Debian's own PostgreSQL major version; if
# pg_dump ever reports a "server version mismatch" against Supabase (whose
# Postgres major version can be newer), switch to the PGDG apt repository
# for a newer pg_dump build — see BACKUP_RESTORE.md.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

RUN mkdir -p /data/uploads && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3)"

# Schema migrations are an explicit release step (documented in DEPLOYMENT.md),
# never an application-instance startup action. This keeps scaled replicas from
# racing to migrate the same production database.
CMD ["sh", "-c", "uvicorn main:app --app-dir backend --host ${HOST} --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
