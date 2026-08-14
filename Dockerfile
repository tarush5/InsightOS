FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 SEED_DIR=/app/seed
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev,ml]"

COPY apps/api/ ./
COPY scripts/ ./scripts/

# Generate synthetic demo data inside image for seamless investigations
RUN python scripts/seed_data.py --out ./seed --seed 42

# Never run as root.
RUN useradd --create-home --uid 10001 insightos && chown -R insightos:insightos /app
USER insightos

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=3s --start-period=15s \
  CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
