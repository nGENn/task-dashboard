# ==============================================================================
# Build Stage - Install dependencies
# ==============================================================================
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python-build-stage

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Install build dependencies for C extensions (psycopg[c], argon2, etc.)
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first (cached unless dependencies change)
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy source code
COPY . ${APP_HOME}

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# ==============================================================================
# Runtime Stage - Minimal production image
# ==============================================================================
FROM python:3.13-slim-bookworm AS python-run-stage

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Create non-root user
RUN addgroup --system django \
    && adduser --system --ingroup django django

# Install only runtime dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
    libpq5 \
    gettext \
    wait-for-it \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
    && rm -rf /var/lib/apt/lists/*

# Copy scripts
COPY --chmod=755 ./compose/production/django/entrypoint /entrypoint
COPY --chmod=755 ./compose/production/django/start /start

# Copy Python environment and application from build stage
COPY --from=python-build-stage --chown=django:django ${APP_HOME} ${APP_HOME}

# Create media directory
RUN mkdir -p ${APP_HOME}/ticket_dashboard/media \
    && chown -R django:django ${APP_HOME}

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER django

# Compile translations at build time
RUN DATABASE_URL="" \
    DJANGO_SETTINGS_MODULE="config.settings.test" \
    python manage.py compilemessages

# Collect static files with production settings (generates manifest)
RUN DATABASE_URL="" \
    DJANGO_SETTINGS_MODULE="config.settings.production" \
    DJANGO_SECRET_KEY="build-only-not-real" \
    DJANGO_ADMIN_URL="admin/" \
    SENTRY_DSN="" \
    python manage.py collectstatic --noinput

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1

ENTRYPOINT ["/entrypoint"]
