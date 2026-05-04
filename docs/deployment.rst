Deployment
==========

Task Dashboard is deployed as a Docker container with PostgreSQL and Valkey (Redis-compatible).

Requirements
------------

- Docker & Docker Compose
- PostgreSQL 18
- Valkey 9 (or Redis 7+)

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
2. Set a strong, random ``DJANGO_SECRET_KEY``.
3. Configure ``ALLOWED_HOSTS`` / ``DJANGO_ALLOWED_HOSTS``.
4. Run migrations: ``uv run manage.py migrate``.
5. Collect static files: ``uv run manage.py collectstatic``.
6. Start the Django-Q worker alongside the web process.

Database Migrations
-------------------

Migrations are applied automatically in the Docker entrypoint.
For manual migration on first deploy::

    uv run manage.py migrate

Background Workers
------------------

Django-Q2 processes background tasks (service sync, etc.).
Ensure the worker is started alongside the web server::

    uv run manage.py qcluster
