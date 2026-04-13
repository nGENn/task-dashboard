FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml .
RUN --mount=type=bind,source=uv.lock,target=uv.lock uv sync --frozen --no-install-project