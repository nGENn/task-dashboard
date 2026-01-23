# Universal Task Dashboard

An internal operational dashboard aggregating tasks, issues, tasks, and risks from Zammad, GitLab, EspoCRM, OpenProject, and Eramba.

## Bump Version

uv version --bump (major|minor|patch)

## Architecture

### Data Strategy

The application uses a **"Fetch All, Filter Locally"** pattern to ensure speed and unified security rules:

1. **Aggregation:** The backend uses global Admin/API Tokens to fetch **all** active tasks from every service.
2. **Caching:** Results are cached in Redis for 5 minutes. Passing `?refresh=1` in the URL forces a cache bypass.
3. **Security Gatekeeper:** Raw data never reaches the template. A Gatekeeper in `views.py` filters the cached list based on the user's **Django Group Permissions**.
      * **Rule:** A user only sees a task if their group has explicit access to that Task's Origin/Group, OR if the task is assigned to their specific email address.

### Environment Variables & Security

The application uses a stable `DJANGO_SECRET_KEY` to encrypt and decrypt sensitive information (like API tokens) stored in the database.

> [!IMPORTANT]
> **Encryption Key Requirement:**
> You **MUST** set a fixed `DJANGO_SECRET_KEY` in `.envs/.local/.django` *before* configuring any services in the Admin panel.
>
> ```text
> DJANGO_SECRET_KEY=your-stable-secret-key-here
> ```
>
> If you change this key later, all existing encrypted API tokens will become unreadable, and you will need to re-enter them in the Admin panel.

### Database Seeding

To populate the local environment with test data, run the management commands:

```bash
uv run manage.py seed_zammad_demo
uv run manage.py seed_gitlab_demo
uv run manage.py seed_espocrm_demo
```

### CSS / Frontend

The project uses Tailwind CSS v4 via a standalone binary. If you modify HTML classes, you must rebuild the CSS file for changes to appear.

**One-time build:**

```bash
cd ticket_dashboard/static/css
./tailwindcss -i input.css -o project.css
```

**Watch mode (recommended during dev):**

```bash
cd ticket_dashboard/static/css
./tailwindcss -i input.css -o project.css --watch
```

## Configuration & Access Control

### Service Management (Service Configuration)

Services (Zammad, GitLab, etc.) are managed dynamically in the **Admin Panel** under **Users \> Service Configurations**.

* **Multi-Instance Support:** You can add multiple instances of the same service (e.g., two different GitLab servers).
* **Dynamic Configuration:** API URLs, tokens, and status (`is_active`) are configured here.
* **Token Security:** All `API Token` values are **encrypted at rest** in the database using the `DJANGO_SECRET_KEY`.
* **Resource Optimization:** Disabled services are skipped during the API fetch process.

### Permissions (RBAC)

Access control is decoupled from the services and managed via **Django Groups**.

1. **Auto-Discovery:** Loading the dashboard triggers a scan of all fetched tasks. New groups (e.g., "Zammad - Support") are automatically added to **Admin \> Users \> External Groups**.
2. **Assignment:**
      * Go to **Admin \> Auth \> Groups**.
      * Edit an internal group (e.g., "Sales Team").
      * Add a **Task Permission** entry inline.
3. **Access Levels:**
      * **FULL:** View all tasks in that specific external group.
      * **LIMITED:** View only tasks in that group that are **Unassigned** or assigned to the **current user**.

## Service Integration Details

* **Zammad:** Fetches via List API. Elasticsearch is disabled in dev environment to reduce overhead. Checks for duplicates before seeding.
* **GitLab:** Fetches Issues and Merge Requests. Uses an Admin lookup to map User IDs to Email addresses for permission filtering.
* **EspoCRM:** Fetches Cases and Tasks. Requires the API Key to have a specific Role with "Read All" access to User/Case/Task entities.
* **OpenProject:** Fetches Work Packages. In local dev, the service injects a `Host` header to bypass OpenProject's strict hostname validation.
* **Eramba:** Fetches Security Incidents and Operations. Parses nested JSON structures unique to Eramba modules.
