#!/bin/bash
set -o errexit

python manage.py collectstatic --noinput
exec gunicorn config.wsgi --bind 0.0.0.0:8000 --chdir=/app
