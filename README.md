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
cd ticket_dashboard/static/css
./tailwindcss -i input.css -o project.css
```

**Watch mode:**

```bash
cd ticket_dashboard/static/css
./tailwindcss -i input.css -o project.css --watch
```

## 🏗️ Architecture

### Data Strategy

The application uses a **"Fetch All, Filter Locally"** pattern:

1. **Aggregation:** API Tokens fetch **all** active tasks.
2. **Caching:** Results cached for 5 minutes (`?refresh=1` to bypass).
3. **Security Gatekeeper:** `views.py` filters cached lists based on **Django Group Permissions**.

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

1. **Auto-Discovery:** Loading the dashboard discovers external groups (e.g., "Zammad - Support").
2. **Assignment:** In **Admin > Auth > Groups**, add a **Task Permission** entry.
3. **Access Levels:**
   - **FULL:** View all tasks in the group.
   - **LIMITED:** View only Unassigned or Own tasks.
   - **MINIMAL:** View only own tasks.

## 🔌 Service Integration Details

- **Zammad:** Fetches via List API. Duplicates checked before seeding.
- **GitLab:** Fetches Issues and Merge Requests. Maps User IDs to Emails.
- **EspoCRM:** Fetches Cases and Tasks. Requires "Read All" role.
- **OpenProject:** Injects `Host` header to bypass hostname validation.

### WIP

- **Eramba:** Parses nested JSON structures for Incidents/Operations.
