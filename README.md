# Universal Task Dashboard

A high-performance aggregation engine for tracking duties across multiple platforms (Zammad, GitLab, EspoCRM, OpenProject, and Eramba).

## 🚀 Key Features

- **The Identity Bridge**: Unifies fragmented user identities (emails, full names, usernames) into a single canonical view using PostgreSQL-native tokenization and GIN indexes.
- **Fetch-Sync-Prune Engine**: High-concurrency synchronization powered by **Django-Q2**, **Valkey 9**, and **httpx**.
- **Perspective Routing**: Stateful dashboard views (`/my`, `/unassigned`, `/all`) with HTMX-driven partial updates and bookmarkable URLs.
- **Multilingual**: Full Internationalization (i18n) support with extensive German localization and dynamic status mapping.
- **Secure by Design**: Role-Based Access Control (RBAC) enforced at the database level, with OIDC/Keycloak integration and encrypted API credentials.

## 🏗️ Data Strategy & Architecture

The application uses an asynchronous pattern for high performance and minimal perceived latency:

1.  **Parallel Fetching**: **Django-Q2** dispatches workers for each service simultaneously. All sources are updated in parallel.
2.  **Concurrent Pagination**: Service clients use `asyncio` to fetch multiple API pages concurrently, maximizing I/O throughput.
3.  **Atomic Upsert**: Data is written via `bulk_create` with `update_conflicts=True`, ensuring high-speed ingestion of thousands of records.
4.  **Automatic Pruning**: Local tasks are automatically purged if they no longer exist in the remote service, keeping the dashboard lean.

## 🔌 Service Integration Details

- **Zammad**: Concurrent fetching of up to 10 pages. Automatically maps local User IDs to canonical Emails through the Identity Bridge.
- **GitLab**: Parallel fetching of Issues and Merge Requests. Tracks "updated_at" for real-time priority sorting.
- **EspoCRM**: Parallel fetching of Cases and Tasks. Requires a user role with "Read All" permissions in Espo.
- **OpenProject**: Optimized work package fetching with support for custom internal host header mapping.
- **Eramba**: Integration with Security Incidents, Projects, Achievements, and Review modules. Includes smart future-task filtering and robust departmental group parsing.

## ⚙️ Role-Based Access Control (RBAC)

Access is managed via **Django Groups** and mapped to discovered external service groups:

- **FULL**: View all tasks in the associated service, external group, or project.
- **LIMITED**: View only tasks assigned to the user OR tasks that are currently unassigned.
- **OWN**: View only tasks explicitly assigned to the user (strict identity matching).

## 📦 Getting Started

### Prerequisites
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Docker & Docker Compose
- **Valkey 9** (Recommended) or Redis (Fallback)

### Local Development
1. **Setup Env**: `cp .env.example .env` (Add your Service API keys and Keycloak credentials).
2. **Infrastructure**: Start PostgreSQL and Valkey.
3. **Install & Migrate**: `uv sync && uv run manage.py migrate`
4. **Run Services**:
   - `uv run manage.py runserver` (Web Interface at localhost:8000)
   - `uv run manage.py qcluster` (Background Worker - **Required for Task Sync**)
5. **CSS Workflow**: `./tailwindcss -i task_dashboard/static/css/input.css -o task_dashboard/static/css/output.css --watch`

## 🚀 Deployment

The production stack is orchestrated via [**`docker-compose.production.yml`**](docker-compose.production.yml).

### Production Configuration
1. **Environment**: Configure `.env` with production-grade secrets.
2. **Valkey Infrastructure**: Ensure `VALKEY_URL` uses the `redis://` scheme for library compatibility (e.g., `redis://valkey:6379/0`).
3. **Orchestration**:
   ```bash
   docker compose -f docker-compose.production.yml up --build -d
   ```

### Production Stack
- **`django`**: The core application (Gunicorn/WhiteNoise).
- **`qcluster`**: The background worker process (Parallel fetch engine).
- **`postgres`**: Database (PostgreSQL 18 with `unaccent`).
- **`valkey`**: In-memory store (Valkey 9.0.3) for task queues and caching.

For deep-dive technical logic, see [**CONTRIBUTING.md**](CONTRIBUTING.md).
