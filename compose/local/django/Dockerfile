FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python

ARG APP_HOME=/app
WORKDIR ${APP_HOME}

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential \
  libpq-dev \
  gettext \
  wait-for-it \
  git \
  && rm -rf /var/lib/apt/lists/*

# Install dependencies (cached unless lock/pyproject change)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --no-install-project

COPY . ${APP_HOME}

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync

ENV PATH="${APP_HOME}/.venv/bin:$PATH"

COPY --chmod=755 ./compose/production/django/entrypoint /entrypoint
COPY --chmod=755 ./compose/local/django/start /start

ENTRYPOINT ["/entrypoint"]
