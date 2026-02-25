# ==============================================================================
# Build Stage - Install dependencies
# ==============================================================================
FROM ghcr.io/astral-sh/uv:python3.13-alpine AS python-build-stage

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Install build dependencies for compiling C extensions (psycopg, argon2, etc.)
RUN apk add --no-cache \
    gcc \
    musl-dev \
    postgresql-dev \
    libpq-dev \
    libffi-dev

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
FROM python:3.13-alpine AS python-run-stage

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Create non-root user
RUN addgroup -S django \
    && adduser -S -G django django

# Install only runtime dependencies
RUN apk add --no-cache \
    libpq \
    postgresql-libs \
    gettext \
    bash

# Install wait-for-it (not in Alpine repos)
ADD --chmod=755 https://raw.githubusercontent.com/vishnubob/wait-for-it/master/wait-for-it.sh /usr/local/bin/wait-for-it

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
    CMD wget --no-verbose --tries=1 --spider http://localhost:5000/ || exit 1

ENTRYPOINT ["/entrypoint"]
