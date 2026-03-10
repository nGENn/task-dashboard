# Universal Task Dashboard

An internal operational dashboard aggregating tasks, issues, and risks from Zammad, GitLab, EspoCRM, OpenProject, and Eramba.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Docker & Docker Compose

### Installation

1. **Environment Setup:**
   Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

   Update `DJANGO_SECRET_KEY` and other settings in `.env` as needed.

2. **Run the Worker:**
   This project uses Django-Q for background tasks. In a separate terminal, run:

   ```bash
   uv run manage.py qcluster
   ```

### Database Seeding

To populate the local environment with test data:

```bash
uv run manage.py seed_zammad_demo
uv run manage.py seed_gitlab_demo
uv run manage.py seed_espocrm_demo
```

### CSS / Frontend

The project uses standalone Tailwind CSS.

**One-time build:**

```bash
cd task_dashboard/static/css
./tailwindcss -i input.css -o project.css
```

**Watch mode:**

```bash
cd task_dashboard/static/css
./tailwindcss -i input.css -o project.css --watch
```

## 🏗️ Architecture

### Data Strategy

The application uses an asynchronous **"Fetch-Sync-Prune"** pattern for high performance and reliability:

1.  **Parallel Background Fetching:** When a refresh is triggered, **Django-Q** dispatches parallel tasks for each service. All services are fetched simultaneously.
2.  **Concurrent Pagination:** Individual service clients use **`httpx`** and **`asyncio`** to fetch multiple API pages concurrently, drastically reducing I/O wait times.
3.  **High-Speed Batch Upserting:** Thousands of tasks are processed and written to the PostgreSQL database in bulk using `bulk_create` with `update_conflicts=True`.
4.  **Automatic Pruning:** After each sync, the system automatically deletes local tasks that are no longer present in the remote service (e.g., closed or deleted tasks), keeping the database perfectly in sync.
5.  **Security Gatekeeper:** `views.py` filters the database records in real-time based on **Django Group Permissions (RBAC)**.

### Environment Variables & Security
>
> [!IMPORTANT]
> **Encryption Key Requirement:**
> You **MUST** set a fixed `DJANGO_SECRET_KEY` in `.env` *before* configuring services.
> If this key changes, encrypted API tokens in the database will become unreadable.

## ⚙️ Configuration & Access Control

### Service Management

Manage services in **Admin > Users > Service Configurations**.

- **Multi-Instance:** Add multiple instances of the same service.
- **Security:** API Tokens are encrypted at rest.
- **Optimization:** Disabled services are skipped.

### Permissions (RBAC)

Access control is decoupled from services and managed via **Django Groups**.

1. **Auto-Discovery:** Synchronizing a service automatically discovers external groups (e.g., "Zammad - Support").
2. **Assignment:** In **Admin > Auth > Groups**, add a **Task Permission** entry.
3. **Access Levels:**
   - **FULL:** View all tasks in the group.
   - **LIMITED:** View only Unassigned or Own tasks.
   - **MINIMAL:** View only own tasks.

## 🔌 Service Integration Details

- **Zammad:** Concurrent fetching of up to 10 pages. Maps User IDs to Emails.
- **GitLab:** Parallel fetching of Issues and Merge Requests.
- **EspoCRM:** Parallel fetching of Cases and Tasks. Requires "Read All" role.
- **OpenProject:** Optimized work package fetching with `Host` header injection.

### WIP

- **Eramba:** Parallel fetching of Security Incidents, Projects, Achievements, and various Review modules. Includes smart future-task filtering and robust departmental group parsing.
