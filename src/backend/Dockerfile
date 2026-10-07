FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:/root/.cargo/bin:$PATH"

WORKDIR /app

# Install build dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specifications first for efficient layer caching
COPY pyproject.toml uv.lock ./

# Install project dependencies without the root project
RUN uv sync --frozen --no-install-project

# Copy application code and assets
COPY src ./src
COPY resources ./resources
COPY alembic ./alembic
COPY alembic.ini ./
COPY specs ./specs
COPY docs ./docs
COPY scripts/maintenance/seed_demo_data.py ./scripts/maintenance/seed_demo_data.py

# Sync and install the root project
RUN uv sync --frozen

# Place the virtual environment binary directory on PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
