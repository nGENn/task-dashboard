
# CSS build stage: Use a fixed version of the standalone Tailwind CLI for stability
FROM debian:bookworm-slim AS css-build-stage

WORKDIR /app

# Pin Tailwind CLI version for reproducible builds
ARG TAILWIND_VERSION=v4.0.9

RUN apt-get update && apt-get install -y curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64 \
    && chmod +x tailwindcss-linux-x64 \
    && mv tailwindcss-linux-x64 /usr/local/bin/tailwindcss

# Copy project files for Tailwind to scan
COPY ./task_dashboard /app/task_dashboard

# Build the CSS
RUN cd task_dashboard/static/css && \
    tailwindcss -i input.css -o output.css --minify

# Python build stage
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python-build-stage

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Install build dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential \
  libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Install requirements first for better caching
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Now copy the project and install it
COPY . ${APP_HOME}
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Python 'run' stage
FROM python:3.13-slim-bookworm AS python-run-stage

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

RUN addgroup --system django \
  && adduser --system --ingroup django django

# Install required system dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  libpq-dev \
  gettext \
  wait-for-it \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

COPY --chown=django:django ./compose/production/django/entrypoint /entrypoint
RUN sed -i 's/\r$//g' /entrypoint && chmod +x /entrypoint

COPY --chown=django:django ./compose/production/django/start /start
RUN sed -i 's/\r$//g' /start && chmod +x /start

# Copy the application (including .venv) from the builder
COPY --from=python-build-stage --chown=django:django ${APP_HOME} ${APP_HOME}

# Copy the compiled CSS from the css-build-stage
COPY --from=css-build-stage --chown=django:django /app/task_dashboard/static/css/output.css ${APP_HOME}/task_dashboard/static/css/output.css

# Prepare media folder
RUN mkdir -p ${APP_HOME}/task_dashboard/media && chown django:django ${APP_HOME}/task_dashboard/media

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

USER django

# Run collectstatic and compilemessages during build
# We use dummy values to satisfy Django's check without a real DB/Redis
ENV DATABASE_URL="postgres://localhost/dummy" \
    DJANGO_SECRET_KEY="dummy" \
    VALKEY_URL="redis://localhost:6379/0" \
    SENTRY_DSN="https://dummy@sentry.io/1" \
    DJANGO_SETTINGS_MODULE="config.settings.production"

RUN python manage.py compilemessages && \
    python manage.py collectstatic --noinput

CMD ["/start"]
ENTRYPOINT ["/entrypoint"]
