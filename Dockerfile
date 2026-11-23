FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PT_ENVIRONMENT=production \
    PT_API_HOST=0.0.0.0 \
    PT_API_PORT=8765 \
    PT_DATA_DIR=/data \
    PT_LOG_DIR=/data/logs \
    PT_CONFIG_DIR=/data/config

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY alembic ./alembic

RUN pip install --no-cache-dir ".[scraping,postgres]" \
    && playwright install --with-deps chromium \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/logs /data/config \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3)" || exit 1

CMD ["sh", "-c", "alembic upgrade head && exec price-tracker-server"]
