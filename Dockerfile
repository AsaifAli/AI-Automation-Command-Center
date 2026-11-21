# syntax=docker/dockerfile:1.7

# Shared runtime dependency layer for test and production images.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --retries 10 --timeout 120 -r requirements.txt

COPY app ./app
COPY ui ./ui
COPY docs ./docs
COPY evals ./evals
COPY scripts ./scripts
COPY config.json README.md Makefile pyproject.toml .env.example ./

RUN chown -R appuser:appuser /app

# The test stage is opt-in. It installs development-only tools and validates
# the exact source tree before a container image is accepted as test-clean.
FROM base AS test

COPY requirements-dev.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --retries 10 --timeout 120 -r requirements-dev.txt

COPY tests ./tests

RUN ruff check app ui tests scripts \
    && python -m compileall -q app ui tests scripts \
    && pytest -q

CMD ["python", "-m", "pytest", "-q"]

# Keep the default image as the lean API runtime image used by Render.
FROM base AS runtime

USER appuser
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
