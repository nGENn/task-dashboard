# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django-based multi-service task aggregation dashboard that pulls tasks/tasks from Zammad, GitLab, EspoCRM, OpenProject, and Eramba into a unified interface with RBAC-based access control. Uses Keycloak for SSO via django-allauth.

## Tech Stack

- **Backend:** Django 5.2, Python 3.13, PostgreSQL 18, Redis, Django-Q2 (background tasks)
- **Frontend:** Django templates, Tailwind CSS (standalone binary), DaisyUI, Flatpickr
- **Auth:** django-allauth (local + Keycloak OIDC), Argon2 password hashing
- **Package manager:** uv

## Common Commands

```bash
# Dependencies
uv sync

# Run dev server (requires PostgreSQL + .env configured)
uv run manage.py runserver_plus 0.0.0.0:8000

# Docker development
docker compose -f docker-compose.local.yml up

# Run all tests
uv run pytest

# Run a single test file
uv run pytest task_dashboard/users/tests/test_views.py

# Run a single test
uv run pytest task_dashboard/users/tests/test_views.py::TestDashboardView::test_method_name -v

# Linting (pre-commit runs ruff, djlint, django-upgrade)
uv run pre-commit run --all-files

# Type checking
uv run mypy task_dashboard

# Tailwind CSS (rebuild on change)
cd task_dashboard/static/css && ./tailwindcss -i input.css -o project.css --watch

# Migrations
uv run manage.py makemigrations
uv run manage.py migrate

# Seed demo data
uv run manage.py seed_zammad_demo
uv run manage.py seed_gitlab_demo
uv run manage.py seed_espocrm_demo

# Version bumping (CI requires a bump on every MR)
uv version --bump patch   # 0.1.x -> 0.1.(x+1)  for bug fixes / small changes
uv version --bump minor   # 0.x.0 -> 0.(x+1).0  for new features
uv version --bump major   # x.0.0 -> (x+1).0.0  for breaking changes
uv version 1.2.3          # set an explicit version
```

## Architecture

### Settings (config/settings/)

Split settings: `base.py` (shared), `local.py` (dev with LocMem cache + debug toolbar), `production.py` (Redis cache + Sentry + SSL), `test.py` (fast hashing, no external deps). Test settings are used by pytest via `--ds=config.settings.test` in pyproject.toml.

### Single Django App (task_dashboard/)

All business logic lives in `task_dashboard/users/` — models, views, tasks, admin, templatetags.

### Key Models (task_dashboard/users/models.py)

- **User** — custom AbstractUser, email as USERNAME_FIELD (no username)
- **ServiceConfiguration** — stores external service URLs + encrypted API tokens (EncryptedCharField), toggled via `is_active`
- **Task** — normalized task from any service, unique on `(service, external_id)`
- **ExternalGroup** — auto-discovered groups from services (origin + name)
- **TaskPermission** — RBAC: links Django Groups → ExternalGroups with access levels (FULL / LIMITED / OWN_ONLY)
- **SavedView** — user's saved filter configurations as JSON

### Service Integrations (task_dashboard/services/)

Each file (`zammad.py`, `gitlab.py`, `espocrm.py`, `openproject.py`, `eramba.py`) is a service class that fetches tasks via API, normalizes them to the Task model format, and caches results (5 min). Uses httpx for HTTP calls.

### Data Flow

1. Services fetch all active tasks from external APIs (cached 5 min, force refresh with `?refresh=1`)
2. `DashboardView` applies RBAC filtering in memory based on user's group permissions
3. UI filters (status, owner, search, date) applied on top
4. Results paginated (50/page) and rendered server-side

### Authentication Flow

- Local email/password signup (when `ACCOUNT_ALLOW_REGISTRATION=True`)
- Keycloak OIDC via allauth's openid_connect provider
- Custom `SocialAccountAdapter` (adapters.py) syncs Keycloak groups → Django Groups on each login

## Code Quality

- **Ruff** for linting + formatting (extensive rule set in pyproject.toml, isort with force-single-line)
- **djLint** for Django template linting (profile=django)
- **mypy** with django-stubs for type checking
- **pre-commit** hooks run all of the above automatically
- Migrations excluded from linting via `extend-exclude`

## CI/CD (.gitlab-ci.yml)

Three stages: lint (pre-commit), test (pytest in Docker), build (multi-arch Docker image). MRs require a version bump in `pyproject.toml` (enforced by `check_version_bump` job).

## Environment

Copy `.env.example` to `.env`. Key variables: `DATABASE_URL`, `REDIS_URL`, `DJANGO_SECRET_KEY` (must remain stable — used for EncryptedCharField), `KEYCLOAK_*` for OIDC, `DJANGO_ADMIN_URL`.
