# ---- Build stage ----
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python-build-stage

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential \
  libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached unless lock/pyproject change)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . ${APP_HOME}

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# ---- Run stage ----
FROM python:3.13-slim-bookworm AS python-run-stage

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

RUN addgroup --system django \
  && adduser --system --ingroup django django

RUN apt-get update && apt-get install --no-install-recommends -y \
  libpq-dev \
  gettext \
  wait-for-it \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# Copy scripts and set permissions in one layer
COPY --chmod=755 ./compose/production/django/entrypoint /entrypoint
COPY --chmod=755 ./compose/production/django/start /start

# Copy the venv and application from the builder
COPY --from=python-build-stage --chown=django:django ${APP_HOME} ${APP_HOME}

RUN mkdir -p ${APP_HOME}/ticket_dashboard/media \
  && chown django:django ${APP_HOME}

ENV PATH="/app/.venv/bin:$PATH"

USER django

# Compile translations and collect static files at build time
RUN DATABASE_URL="" \
  DJANGO_SETTINGS_MODULE="config.settings.test" \
  python manage.py compilemessages

RUN DATABASE_URL="" \
  DJANGO_SETTINGS_MODULE="config.settings.test" \
  python manage.py collectstatic --noinput

ENTRYPOINT ["/entrypoint"]
