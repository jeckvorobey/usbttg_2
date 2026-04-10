FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    DB_PATH=/data/history.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN pip install --no-cache-dir uv \
    && uv export --frozen --no-dev --format requirements-txt -o requirements.txt \
    && uv pip install --system --requirement requirements.txt \
    && rm requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /app/data \
    && chown -R appuser:appuser /app /data

USER appuser

CMD ["python", "run.py"]
