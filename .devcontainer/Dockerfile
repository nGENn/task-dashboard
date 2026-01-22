FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim

# Set Environment Variables
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
ENV LANGUAGE=en_US:en

# Install Dev Tools
RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential libpq-dev gettext wait-for-it \
    libffi-dev python3-dev \
    sudo git bash-completion nano ssh \
    locales \
    && echo "en_US.UTF-8 UTF-8" > /etc/locale.gen \
    && locale-gen \
    && apt-get clean

# Create Dev User
RUN groupadd --gid 1000 dev-user \
    && useradd --uid 1000 --gid dev-user --shell /bin/bash --create-home dev-user \
    && echo dev-user ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/dev-user \
    && chmod 0440 /etc/sudoers.d/dev-user

WORKDIR /app

# Ensure correct permissions before switching user
RUN chown -R dev-user:dev-user /app

# Switch to non-root user for subsequent steps
USER dev-user

# Install Python dependencies
COPY --chown=dev-user:dev-user pyproject.toml uv.lock ./
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"
