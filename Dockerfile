FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY platform ./platform

RUN mkdir -p /data/uploads && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000
# VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3)"

# Schema migrations are an explicit release step (documented in DEPLOYMENT.md),
# never an application-instance startup action. This keeps scaled replicas from
# racing to migrate the same production database.
CMD ["sh", "-c", "uvicorn main:app --app-dir backend --host ${HOST} --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
