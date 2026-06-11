# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django-based multi-service task aggregation dashboard that pulls tasks from Zammad, GitLab, EspoCRM, OpenProject, and Eramba into a unified interface with RBAC-based access control. A sixth integration, Kimai, is a push/export target (not a task source): it provisions customers/projects/activities/teams and drives time-tracking reminders. Uses Keycloak for SSO via django-allauth, configurable either through `KEYCLOAK_*` env vars or the admin-managed `SSOConfiguration` singleton (which takes precedence).

## Tech Stack

- **Backend:** Django 5.2, Python 3.13, PostgreSQL 18, Valkey 9.0.3, Django-Q2 (background tasks)
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
```

## Architecture

### Settings (config/settings/)

Split settings: `base.py` (shared), `local.py` (dev with LocMem cache + debug toolbar), `production.py` (Valkey cache + Sentry + SSL), `test.py` (fast hashing, no external deps). Test settings are used by pytest via `--ds=config.settings.test` in pyproject.toml.

### Django Apps (task_dashboard/)

Most business logic lives in `task_dashboard/users/` — models, views, tasks, admin, templatetags. The Kimai integration is a separate app, `task_dashboard/kimai/` (its own models, tasks, client, service, tests).

### Key Models (task_dashboard/users/models.py)

- **User** — custom AbstractUser, email as USERNAME_FIELD (no username)
- **ServiceConfiguration** — stores external service URLs + encrypted API tokens (EncryptedCharField), toggled via `is_active`; `service_type` choices come from the `service_specs.py` registry (single source of truth — adding a service is one entry there)
- **Task** — normalized task from any service, unique on `(service, external_id)`
- **ExternalGroup** — auto-discovered groups from services (origin + name)
- **TaskPermission** / **ServicePermission** — RBAC: link Django Groups → ExternalGroups / Services with access levels (FULL / LIMITED / OWN / NONE)
- **TaskOwner** — distinct owner discovered from synced tasks (keyed by email), optionally linked to a Django user; spine for RBAC owner-matching and Kimai user provisioning
- **GlobalSetting / EmailConfiguration / SSOConfiguration** — admin-configurable singletons (pk=1, `.load()`); SSOConfiguration overrides the `KEYCLOAK_*` env vars when enabled
- **SavedView** — user's saved filter configurations as JSON

### Service Integrations (task_dashboard/services/)

Each file (`zammad.py`, `gitlab.py`, `espocrm.py`, `openproject.py`, `eramba.py`) is a service class that fetches tasks via API, normalizes them to the Task model format, and caches results (5 min). Uses httpx for HTTP calls. All subclass `base.py::BaseService`. Kimai's service class lives in `task_dashboard/kimai/service.py`; its `get_tasks_async` returns `[]` (Kimai is a push target, not a source) and it only participates in the health check — the actual push/sync logic is in `task_dashboard/kimai/tasks.py`.

### Data Flow

1. Services fetch all active tasks from external APIs (cached 5 min, force refresh with `?refresh=1`)
2. `DashboardView` applies RBAC filtering via database queries based on user's group permissions
3. UI filters (status, owner, search, date) applied on top
4. Results paginated (50/page) and rendered server-side

### Authentication Flow

- Local email/password signup, gated by `ACCOUNT_ALLOW_REGISTRATION` (defaults **False** — SSO is the intended entry path; enabling local signup should also set `ACCOUNT_EMAIL_VERIFICATION = "mandatory"`)
- Keycloak OIDC via allauth's openid_connect provider, configured via `KEYCLOAK_*` env vars or the `SSOConfiguration` admin singleton (singleton wins when enabled)
- Custom `SocialAccountAdapter` (adapters.py) syncs Keycloak groups → Django Groups on each login. Only token group names in the `sso-` namespace are synced (untrusted); the admin-configured `GlobalSetting.sso_default_group` fallback is applied verbatim

## Code Quality

- **Ruff** for linting + formatting (extensive rule set in pyproject.toml, isort with force-single-line)
- **djLint** for Django template linting (profile=django)
- **mypy** with django-stubs for type checking
- **pre-commit** hooks run all of the above automatically
- Migrations excluded from linting via `extend-exclude`

## CI/CD (.gitlab-ci.yml)

Four stages: lint (pre-commit + mypy), test (pytest against PostgreSQL 18 + Valkey, `makemigrations --check`, `check --deploy`, version-bump check; coverage floored at `--cov-fail-under=65`), security (bandit + pip-audit), build (multi-arch Docker image, gated on all prior jobs passing).

Do NOT bump the version (`pyproject.toml`) yourself — leave it to the human at MR time.

## Environment

Copy `.env.example` to `.env`. Key variables: `DATABASE_URL`, `VALKEY_URL`, `DJANGO_SECRET_KEY` (must remain stable — used for EncryptedCharField), `KEYCLOAK_*` for OIDC, `DJANGO_ADMIN_URL`.
