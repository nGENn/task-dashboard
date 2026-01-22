#!/bin/bash
set -o errexit
set -o pipefail
set -o nounset

# Default to postgres user if not set
if [ -z "${POSTGRES_USER}" ]; then
    export POSTGRES_USER="postgres"
fi

# Construct DATABASE_URL if not explicitly set
export DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"

# Debugging information
echo "Current user: $(whoami)"
echo "Python path: $(which python)"
echo "Python version: $(python --version)"
echo "Venv permissions: $(ls -ld /app/.venv)"
echo "Python executable in venv: $(ls -l /app/.venv/bin/python || echo 'Not found')"
echo "Python3 executable in venv: $(ls -l /app/.venv/bin/python3 || echo 'Not found')"
echo "PATH is: $PATH"
echo "Checking if django is importable..."
if python -c "import django; print(f'Django version: {django.get_version()}')" 2>/dev/null; then
    echo "Django imported successfully"
else
    echo "Failed to import Django"
    echo "Sys path: $(python -c 'import sys; print(sys.path)')"
fi

# Wait for Postgres
echo "Waiting for PostgreSQL..."
wait-for-it "${POSTGRES_HOST}:${POSTGRES_PORT}" -t 30

python manage.py migrate

exec "$@"
