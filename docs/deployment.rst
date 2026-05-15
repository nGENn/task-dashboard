Deployment
==========

Task Dashboard ships as a Docker image and a ready-to-use Compose stack.
The application server (Gunicorn) listens on **port 5000** inside the container.

.. note::

   The container entrypoint waits for PostgreSQL and Valkey to become available
   before starting, but it does **not** run migrations automatically.
   Run ``python manage.py migrate`` once after first deploy (see below).

Option 1: Docker Compose (recommended)
---------------------------------------

The included ``docker-compose.production.yml`` bundles all four containers:
Django/Gunicorn, the Django-Q2 background worker, PostgreSQL 18, and Valkey 9.

**Prerequisites:** Docker & Docker Compose only — no external database or cache needed.

1. Clone the repository::

      git clone https://github.com/nGENn/task-dashboard.git
      cd task-dashboard

2. Configure the environment::

      cp .env.example .env
      # Edit .env — at minimum set DJANGO_SECRET_KEY and ALLOWED_HOSTS

3. Build and start the stack::

      docker compose -f docker-compose.production.yml up -d --build

4. Run migrations (first deploy only)::

      docker compose -f docker-compose.production.yml exec django python manage.py migrate

5. Create an admin user::

      docker compose -f docker-compose.production.yml exec django python manage.py createsuperuser

The web interface is available at ``http://<host>:5000``. Put a reverse proxy
(nginx, Caddy, Traefik) in front to handle TLS and expose port 80/443.

Option 2: Docker image only
-----------------------------

Use this if you are integrating into an existing Kubernetes cluster, Nomad job,
or custom Compose stack. You must provide your own **PostgreSQL 18** and
**Valkey 9** (or Redis 7+).

Pull the latest image from the GitHub Container Registry::

   docker pull ghcr.io/ngenn/task-dashboard:latest

Run the web process::

   docker run -d \
     --env-file .env \
     -p 5000:5000 \
     ghcr.io/ngenn/task-dashboard:latest

Run the background worker (required for task sync) as a second container
using the same image and environment, but override the command::

   docker run -d \
     --env-file .env \
     ghcr.io/ngenn/task-dashboard:latest \
     python manage.py qcluster

Apply migrations once after first deploy::

   docker run --rm --env-file .env \
     ghcr.io/ngenn/task-dashboard:latest \
     python manage.py migrate

Environment Variables
---------------------

Copy ``.env.example`` to ``.env`` and configure the following:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Variable
     - Description
   * - ``DATABASE_URL``
     - PostgreSQL connection string, e.g. ``postgres://user:pass@host:5432/db``
   * - ``VALKEY_URL``
     - Valkey/Redis URL, e.g. ``redis://valkey:6379/0``
   * - ``DJANGO_SECRET_KEY``
     - Secret key — **must remain stable**: it is used to encrypt all stored API tokens
       and passwords via ``EncryptedCharField``. Rotating this key will permanently
       invalidate every credential stored in the database. Back it up alongside the
       database.
   * - ``DJANGO_ADMIN_URL``
     - Admin path prefix (default: ``explicit-declared-follow/``)
   * - ``KEYCLOAK_SERVER_URL``
     - Keycloak server URL for OIDC (optional)
   * - ``KEYCLOAK_CLIENT_ID``
     - Keycloak client ID
   * - ``KEYCLOAK_CLIENT_SECRET``
     - Keycloak client secret

Database Prerequisites
----------------------

PostgreSQL must have the ``unaccent`` extension available (included in the
``postgresql-contrib`` package on most distributions). The first migration run
enables it automatically via ``CREATE EXTENSION IF NOT EXISTS unaccent``; no
manual steps are needed unless your PostgreSQL build excludes contrib packages.

For GDPR "right to be forgotten" requests: after bulk-deleting a user's tasks,
run ``VACUUM ANALYZE users_task`` to ensure PostgreSQL removes any residual
tokenized index entries (owner names and emails are indexed for full-text search).
Furthermore, because traces of personally identifiable information (PII) persist
opaquely within these trigram indexes and Write-Ahead Logs (WAL), it is strongly
recommended to operate the PostgreSQL volume with full-disk encryption and schedule
aggressive auto-vacuums when deploying this system as a SaaS.

Production Checklist
--------------------

1. Set ``DJANGO_DEBUG=False``.
2. Set a strong, random ``DJANGO_SECRET_KEY`` and back it up — rotating it invalidates all stored credentials.
3. Configure ``ALLOWED_HOSTS`` / ``DJANGO_ALLOWED_HOSTS``.
4. Run migrations on first deploy (see deployment options above).
5. Ensure the ``qcluster`` worker is running alongside the web process.

.. note::

   Static files are collected at image build time — ``collectstatic`` does not
   need to be run manually.

Background Workers
------------------

Django-Q2 processes background tasks (service sync, etc.).
The ``qcluster`` worker **must** run alongside the web server or task syncing
will not happen. Both the Compose stack and the image-only examples above
cover this.
