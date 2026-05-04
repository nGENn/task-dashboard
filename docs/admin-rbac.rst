Admin & RBAC
============

Role-Based Access Control (RBAC) in Task Dashboard is managed through the Django admin interface.

Concepts
--------

**Django Groups**
   Standard Django groups used as the primary RBAC unit. Users are assigned to groups, and groups receive permissions.

**External Groups**
   Groups auto-discovered from connected services (e.g., Zammad groups, GitLab projects, EspoCRM teams).
   These are created automatically when tasks are fetched.

**Service Permissions**
   Link a Django Group to a ServiceConfiguration with a default access level.
   Acts as a fallback when no TaskPermission matches.

**Task Permissions**
   Link a Django Group to a specific ExternalGroup with a fine-grained access level.

Access Levels
-------------

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Level
     - Behaviour
   * - ``FULL``
     - See all tasks in the matched group/service.
   * - ``LIMITED``
     - See own tasks plus unassigned tasks.
   * - ``OWN_ONLY``
     - See only tasks assigned to the current user.
   * - ``NONE``
     - No access — tasks from this group/service are hidden.

Configuring Access
------------------

1. Navigate to **Admin → Access Control → Groups**.
2. Open a group to edit it.
3. Under **Service Permissions**, add a row linking the group to a ServiceConfiguration and set the default level.
4. Under **Task Permissions**, add rows for specific ExternalGroups with finer levels.

Task Permission takes precedence over Service Permission when both match.

SSO Group Sync
--------------

When Keycloak OIDC is configured, groups from the token are automatically synced to Django groups on each login.

- **SSO Default Fallback** — Always assign new SSO users to this group if they are not found in the token.

Configure these in **Admin → Configuration → Global Settings**.

.. note::
   SSO sync only removes groups that SSO itself previously assigned (tracked via the ``SSOGroup`` marker model).
   Manually assigned groups are never removed by SSO sync.
