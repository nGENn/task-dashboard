# Universal Task Dashboard

A high-performance aggregation engine for tracking duties across multiple platforms (Zammad, GitLab, EspoCRM, OpenProject, and Eramba).

For detailed configuration, service setup, Keycloak integration, and RBAC reference, see the **[full documentation](https://ngenn.github.io/task-dashboard/)**.

## Key Features

- **The Identity Bridge**: Unifies fragmented user identities (emails, full names, usernames) into a single canonical view using PostgreSQL-native tokenization and GIN indexes.
- **Fetch-Sync-Prune Engine**: High-concurrency synchronization powered by **Django-Q2**, **Valkey 9**, and **httpx**.
- **Perspective Routing**: Stateful dashboard views (`/my`, `/unassigned`, `/all`) with HTMX-driven partial updates and bookmarkable URLs.
- **Multilingual**: Full Internationalization (i18n) support with extensive German localization and dynamic status mapping.
- **Secure by Design**: Role-Based Access Control (RBAC) enforced at the database level, with OIDC/Keycloak integration and encrypted API credentials.

## Role-Based Access Control (RBAC)

Access is managed via **Django Groups** and mapped to discovered external service groups:

- **FULL**: View all tasks in the associated service, external group, or project.
- **LIMITED**: View only tasks assigned to the user OR tasks that are currently unassigned.
- **OWN**: View only tasks explicitly assigned to the user (strict identity matching).

## Deploying with Docker

**Prerequisites:** Docker & Docker Compose.

1. Copy and configure the environment file:
   ```bash
   cp .env.example .env
   ```
   Set `DATABASE_URL`, `VALKEY_URL`, `DJANGO_SECRET_KEY`, and your service API keys. See the [documentation](https://ngenn.github.io/task-dashboard/) for all available options.

2. Start the stack:
   ```bash
   docker compose -f docker-compose.production.yml up -d
   ```

3. Apply migrations and create an admin user:
   ```bash
   docker compose -f docker-compose.production.yml exec django python manage.py migrate
   docker compose -f docker-compose.production.yml exec django python manage.py createsuperuser
   ```

The stack runs four containers: `django` (Gunicorn/WhiteNoise), `qcluster` (background sync worker), `postgres` (PostgreSQL 18), and `valkey` (Valkey 9).

---

For contributing and local development, see [**CONTRIBUTING.md**](CONTRIBUTING.md).
