# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY app/ ./app/

# Non-root runtime user
RUN useradd --no-create-home --uid 10001 appuser && chown -R appuser /app
USER 10001

ENV PGHOST=postgres \
    PGPORT=5432 \
    PGDATABASE=northwind \
    PGUSER=northwind \
    PGPASSWORD=northwind \
    PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers ${WEB_CONCURRENCY:-2}"]
