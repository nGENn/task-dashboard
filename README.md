# Universal Ticket Dashboard

An internal operational dashboard aggregating tickets, issues, tasks, and risks from Zammad, GitLab, EspoCRM, OpenProject, and Eramba.

## Bump Version

uv versino --bump (major|minor|patch)

## Architecture

### Data Strategy

The application uses a **"Fetch All, Filter Locally"** pattern to ensure speed and unified security rules:

1. **Aggregation:** The backend uses global Admin/API Tokens to fetch **all** active tickets from every service.
2. **Caching:** Results are cached in Redis for 5 minutes. Passing `?refresh=1` in the URL forces a cache bypass.
3. **Security Gatekeeper:** Raw data never reaches the template. A Gatekeeper in `views.py` filters the cached list based on the user's **Django Group Permissions**.
      * **Rule:** A user only sees a ticket if their group has explicit access to that Ticket's Origin/Group, OR if the ticket is assigned to their specific email address.

## Development Environment

### Setup

This project is configured for **VS Code DevContainers**.

1. Open the project in VS Code.
2. Run **"Dev Containers: Reopen in Container"**.
3. The environment (Python, Node, Docker-in-Docker) will auto-configure.

**Note on Resource Usage:**
The local stack includes a full GitLab instance which is memory intensive. If your local machine struggles, it is recommended to deploy the DevContainer on a remote development server.

### Environment Variables

Ensure your `.envs/.local/.django` file maps API URLs to internal Docker hosts:

* Zammad: `http://zammad-nginx:8080`
* EspoCRM: `http://espocrm:80`
* OpenProject: `http://openproject:80` (Requires `OPENPROJECT_HOST_HEADER=localhost:8082`)
* GitLab: `http://gitlab:8084`

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

### Service Management

Located in **Admin \> Users \> Service Configurations**.

* Toggle `is_active` to hide/show a service globally.
* Disabled services are skipped during the API fetch process to save resources.

### Permissions (RBAC)

Access control is decoupled from the services and managed via **Django Groups**.

1. **Auto-Discovery:** Loading the dashboard triggers a scan of all fetched tickets. New groups (e.g., "Zammad - Support") are automatically added to **Admin \> Users \> External Groups**.
2. **Assignment:**
      * Go to **Admin \> Auth \> Groups**.
      * Edit an internal group (e.g., "Sales Team").
      * Add a **Ticket Permission** entry inline.
3. **Access Levels:**
      * **FULL:** View all tickets in that specific external group.
      * **LIMITED:** View only tickets in that group that are **Unassigned** or assigned to the **current user**.

## Service Integration Details

* **Zammad:** Fetches via List API. Elasticsearch is disabled in dev environment to reduce overhead. Checks for duplicates before seeding.
* **GitLab:** Fetches Issues and Merge Requests. Uses an Admin lookup to map User IDs to Email addresses for permission filtering.
* **EspoCRM:** Fetches Cases and Tasks. Requires the API Key to have a specific Role with "Read All" access to User/Case/Task entities.
* **OpenProject:** Fetches Work Packages. In local dev, the service injects a `Host` header to bypass OpenProject's strict hostname validation.
* **Eramba:** Fetches Security Incidents and Operations. Parses nested JSON structures unique to Eramba modules.
