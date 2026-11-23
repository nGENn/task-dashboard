
# CSS build stage: Use a fixed version of the standalone Tailwind CLI for stability
FROM debian:bookworm-slim AS css-build-stage

WORKDIR /app

# Pin Tailwind CLI version for reproducible builds
ARG TAILWIND_VERSION=v4.3.3
# sha256 of the release binaries; update together with TAILWIND_VERSION
ARG TAILWIND_SHA256_X64=dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a
ARG TAILWIND_SHA256_ARM64=55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195

RUN apt-get update && apt-get install --no-install-recommends -y curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then TAILWIND_ARCH="x64"; TAILWIND_SHA256="$TAILWIND_SHA256_X64"; elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then TAILWIND_ARCH="arm64"; TAILWIND_SHA256="$TAILWIND_SHA256_ARM64"; else echo "Unsupported architecture: $ARCH" && exit 1; fi && \
    curl -fsSLO https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-${TAILWIND_ARCH} && \
    echo "${TAILWIND_SHA256}  tailwindcss-linux-${TAILWIND_ARCH}" | sha256sum -c - && \
    chmod +x tailwindcss-linux-${TAILWIND_ARCH} && \
    mv tailwindcss-linux-${TAILWIND_ARCH} /usr/local/bin/tailwindcss

# Copy project files for Tailwind to scan
COPY ./task_dashboard /app/task_dashboard

# Build the CSS
RUN cd task_dashboard/static/css && \
    tailwindcss -i input.css -o project.css --minify && \
    rm input.css

# Python build stage
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python-build-stage

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

# Install build dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential \
  libpq-dev \
  && apt-get upgrade -y \
  && rm -rf /var/lib/apt/lists/*

# Install requirements first for better caching
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Now copy the project and install it
COPY . ${APP_HOME}
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev && \
    rm -rf task_dashboard/static/css/input.css

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
  procps \
  && apt-get upgrade -y \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# The app runs from the uv-built .venv; drop the base image's system pip, whose
# vendored libs (urllib3, msgpack, pkg_resources) carry unpatched CVEs.
RUN python -m pip uninstall -y pip

COPY --chown=django:django ./compose/production/django/entrypoint /entrypoint
RUN sed -i 's/\r$//g' /entrypoint && chmod +x /entrypoint

COPY --chown=django:django ./compose/production/django/start /start
RUN sed -i 's/\r$//g' /start && chmod +x /start

# Copy the application (including .venv) from the builder
COPY --from=python-build-stage --chown=django:django ${APP_HOME} ${APP_HOME}

# Copy the compiled CSS from the css-build-stage
COPY --from=css-build-stage --chown=django:django /app/task_dashboard/static/css/project.css ${APP_HOME}/task_dashboard/static/css/project.css

# Prepare folders and permissions
RUN mkdir -p ${APP_HOME}/task_dashboard/media ${APP_HOME}/staticfiles && \
    chown -R django:django ${APP_HOME}/task_dashboard/media ${APP_HOME}/staticfiles

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Run collectstatic and compilemessages during build
# We use dummy values to satisfy Django's check without a real DB/Redis.
# They are scoped to this RUN so the runtime image never falls back to a
# known SECRET_KEY when the deployment's .env omits one.
ENV DJANGO_SETTINGS_MODULE="config.settings.production"

RUN export DATABASE_URL="postgres://localhost/dummy" \
           DJANGO_SECRET_KEY="dummy" \
           VALKEY_URL="redis://localhost:6379/0" \
           SENTRY_DSN="https://dummy@sentry.io/1" && \
    python manage.py compilemessages && \
    python manage.py collectstatic --noinput

USER django

CMD ["/start"]
ENTRYPOINT ["/entrypoint"]
