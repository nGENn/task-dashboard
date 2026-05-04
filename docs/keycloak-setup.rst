Keycloak SSO Setup
==================

Task Dashboard uses Keycloak as its OIDC provider via django-allauth. This page
walks through creating the Keycloak client and mapping groups so they sync to
Django groups on each login.


Keycloak is **optional** — if you only need local email/password accounts, leave
all ``KEYCLOAK_*`` variables empty and set ``ACCOUNT_ALLOW_REGISTRATION=True``.

Step 1: Create a Realm
----------------------

In the Keycloak Admin Console, create or select the realm you want to use.
The realm URL will be the base of your ``KEYCLOAK_SERVER_URL``.

Step 2: Create the Client
--------------------------

1. Navigate to **Clients → Create client**.
2. Set **Client type** to ``OpenID Connect``.
3. Set **Client ID** — this becomes your ``KEYCLOAK_CLIENT_ID``.
4. Enable **Client authentication** (confidential client).
5. Set **Valid redirect URIs** to ``https://<your-domain>/accounts/keycloak/login/callback/``.
6. Set **Valid post logout redirect URIs** to ``https://<your-domain>/``.
7. Save the client and copy the **Client secret** from the **Credentials** tab —
   this is your ``KEYCLOAK_CLIENT_SECRET``.

Step 3: Add the Groups Mapper
------------------------------

Task Dashboard reads a ``groups`` claim from the ID token to sync group
memberships. Add a mapper to include it:

1. Open the client → **Client scopes** → click the dedicated scope (e.g.
   ``<client-id>-dedicated``).
2. **Add mapper → By configuration → Group Membership**.
3. Set **Name** to ``groups``.
4. Set **Token Claim Name** to ``groups``.
5. Enable **Add to ID token** and **Add to access token**.
6. Set **Full group path** to ``Off`` so claim values are plain group names
   (e.g. ``support``, not ``/support``).
7. Save.

Step 4: Configure Environment Variables
-----------------------------------------

Add the following to your ``.env``::

    KEYCLOAK_CLIENT_ID=<your-client-id>
    KEYCLOAK_CLIENT_SECRET=<your-client-secret>
    # Base realm URL — no trailing slash
    KEYCLOAK_SERVER_URL=https://id.example.com/realms/myrealm

Step 5: Group Sync Behaviour
------------------------------

On each SSO login the ``SocialAccountAdapter`` reads the ``groups`` claim and:

* Creates any Django group that does not yet exist.
* Assigns the user to all groups listed in the claim.
* Removes the user from any groups that were previously synced via SSO but are
  no longer present in the claim.
* Groups that were manually assigned in the Django Admin are never touched by SSO.

Groups matching ``default-roles-*``, ``offline_access``, and
``uma_authorization`` are silently ignored.

If the Keycloak token contains no ``groups`` claim the user is placed in the
``sso-default-fallback`` group as a safe fallback.

Troubleshooting
---------------

**"Login failed: no token returned"** — Verify the redirect URI matches exactly
(including the trailing slash).

**Groups not syncing** — Check that the mapper is attached to the **dedicated
scope** of the client (not a shared scope) and that ``Full group path`` is off.

**Admin cannot log in via SSO** — By default ``DJANGO_ADMIN_FORCE_ALLAUTH=True``
routes all admin logins through SSO. Set it to ``False`` to allow the standard
Django admin login form as a fallback.
