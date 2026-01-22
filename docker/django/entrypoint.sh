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

# Wait for Postgres
echo "Waiting for PostgreSQL..."
wait-for-it "${POSTGRES_HOST}:${POSTGRES_PORT}" -t 30

exec "$@"
