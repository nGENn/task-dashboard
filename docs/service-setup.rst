Service Integration Setup
==========================

Each service integration requires an API token or key with read access. Add
credentials in the Django Admin under **Task Dashboard → Service Configurations**,
or set them as environment variables as a fallback.

The Admin Panel is the recommended approach — credentials are encrypted at rest
using ``EncryptedCharField``.

To set up SSO mappings for these services, see :doc:`keycloak-setup`.

Zammad
------

**Required fields:** API URL, API Token

1. In Zammad, go to **Profile → Token Access**.
2. Click **Create** and give it a descriptive name (e.g. ``task-dashboard``).
3. Enable the permission ``ticket.reader`` (read-only is sufficient).
4. Copy the generated token.

**Admin Panel fields:**

* **API URL** — your Zammad base URL, e.g. ``https://support.example.com``
* **API Token** — the token from step 3

GitLab
------

**Required fields:** API URL, API Token

1. In GitLab, go to **User Settings → Access Tokens** (or use a Group/Project
   token for narrower scope).
2. Create a token with the ``read_api`` scope.
3. Copy the token.

**Admin Panel fields:**

* **API URL** — your GitLab base URL, e.g. ``https://gitlab.com`` or a
  self-hosted instance
* **API Token** — the token from step 2

EspoCRM
-------

**Required fields:** API URL, API Key

1. In EspoCRM Admin, go to **Administration → API Users**.
2. Create an API user and assign it to a role with read access to
   ``Cases`` and ``Tasks``.
3. On the API User record, generate an **API Key** from the **Security** tab.

**Admin Panel fields:**

* **API URL** — your EspoCRM base URL, e.g. ``https://crm.example.com``
* **API Key** — the key from step 3

OpenProject
-----------

**Required fields:** API URL, API Key (Password field)

1. In OpenProject, go to **My Account → Access Tokens**.
2. Click **Generate** next to **API access token**.
3. Copy the token.

**Admin Panel fields:**

* **API URL** — your OpenProject base URL, e.g. ``https://project.example.com``
* **API Password** — the token from step 2 (OpenProject uses HTTP Basic auth
  with ``apikey:<token>``)

**Optional:** If your OpenProject is behind a reverse proxy that rewrites the
``Host`` header, set the environment variable
``OPENPROJECT_HOST_HEADER=<original-hostname>`` so API requests carry the
correct host.

Eramba
------

**Required fields:** API URL, API Key (username), API Password

1. In Eramba, go to **Settings → API Users** and create a dedicated API user.
2. The username and password for this user are used for HTTP Basic auth.

**Admin Panel fields:**

* **API URL** — your Eramba base URL, e.g. ``https://eramba.example.com``
* **API Key** — the API user's username
* **API Password** — the API user's password

**Optional:** Set ``ERAMBA_OPEN_TASK_FUTURE_WINDOW_DAYS`` (default ``30``) to
control how many days into the future a planned task is still treated as "open".

Testing Connectivity
--------------------

After saving a Service Configuration, use the **Check Health** button in the
Admin Panel (or the service card on the dashboard) to verify the credentials
work and measure response latency.

Use the **Force Refresh** button on the dashboard to trigger an immediate sync
rather than waiting for the background worker's next run.
