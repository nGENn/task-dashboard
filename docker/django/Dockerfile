# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder
ARG INSTALL_DEV=false
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential libpq-dev

# Install dependencies into a virtual environment
# We mount lock files to keep the layer cache stable
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    if [ "$INSTALL_DEV" = "true" ] ; then \
        uv sync --frozen --no-install-project ; \
    else \
        uv sync --frozen --no-install-project --no-dev ; \
    fi

# Stage 2: Runner (Production)
FROM python:3.14-slim-trixie AS runner

# Create a non-root user
RUN addgroup --system --gid 1001 django && \
    adduser --system --uid 1001 --ingroup django django

WORKDIR /app

# Runtime dependencies
RUN apt-get update && apt-get install --no-install-recommends -y \
    libpq-dev gettext wait-for-it \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY --chown=django:django . .

# Copy scripts
COPY --chown=django:django ./docker/django/entrypoint.sh /entrypoint.sh
COPY --chown=django:django ./docker/django/start.sh /start.sh
RUN chmod +x /entrypoint.sh /start.sh

# Create static and media directories and set permissions
RUN mkdir -p /app/staticfiles /app/ticket_dashboard/media && \
    chown -R django:django /app/staticfiles /app/ticket_dashboard/media

ENV PATH="/app/.venv/bin:$PATH"
USER django

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/start.sh"]
