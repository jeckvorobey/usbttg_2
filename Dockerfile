FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    DB_PATH=/data/history.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv \
    pip install uv==0.11.6 \
    && uv export --frozen --no-dev --format requirements-txt -o requirements.txt \
    && uv pip install --system --requirement requirements.txt \
    && rm requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /app/data \
    && chown -R appuser:appuser /app /data

USER appuser

CMD ["python", "run.py"]
