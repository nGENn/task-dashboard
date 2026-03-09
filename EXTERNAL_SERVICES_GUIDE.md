# External Services API Configuration Guide

## Core Principles

1. **Least Privilege:** Only **read permissions** are required.
2. **Dedicated API Users:** Always create a dedicated "API User" (Service Account) in each system rather than using a personal account.
3. **Global Read Access (Recommended):** To avoid adding the API user to every individual project or group, configure "Global Read" or "Admin Read-only" access where possible. This is also required for certain advanced features like mapping User IDs to Emails (e.g., in GitLab).
4. **Scope of Visibility:** The Dashboard can only display items that the configured API User has permission to see.

## GitLab

The dashboard uses the GitLab API to fetch Issues and Merge Requests.

### Recommended: Administrator-level Read Access
Using an Admin-level token allows the dashboard to see all issues and merge requests across the entire instance without manually adding the user to every project. It also allows the system to map GitLab User IDs to real email addresses for secure permission filtering.

1. **Create an API User:** Create a dedicated user account (e.g., `svc-dashboard`) and give it **Administrator** privileges.
2. **Log in as the API User**.
3. Navigate to **User Settings** > **Access Tokens**.
4. Click **Add new token**.
5. Select the following **scope**:
   - `read_api`: Grants read-only access to the entire instance API (when used by an admin).
6. Click **Create personal access token**.
7. Copy the token and enter it into the `API Token` field in the Dashboard configuration.

*Note: If you prefer not to use an Admin user, you must add a regular API user as a member to every project/group with at least "Reporter" access.*

## Eramba

Eramba uses Basic Authentication for API access.

### How to configure
1. **Create an API User:** Log in as admin and navigate to **Settings** > **Users Management** > **Users**. Create a user (e.g., `api-dashboard`).
2. **Enable REST APIs:** In the user configuration, ensure the **"Rest APIs"** checkbox is **enabled**.
3. **Grant Access:** Navigate to **Settings** > **Users Management** > **Access Control**.
4. Assign the user (or its group) **Read-only** permissions for:
   - `Security Incidents`
   - `Projects`
   - `Project Achievements`
5. Ensure these permissions are applied globally or to all relevant organizational units.
6. In the Dashboard configuration, enter the username and password.

## EspoCRM

The dashboard interacts with EspoCRM via API Keys.

### Recommended: Global Read Role
1. **Create an API User:** Navigate to **Administration** > **Integration** > **API Users**.
2. Create a new API User (e.g., `api-dashboard`).
3. **Create a Global Read Role:** Navigate to **Administration** > **Roles**. Create a role "Dashboard Read-only" with:
   - `Cases`: Read -> All
   - `Tasks`: Read -> All
   - `Users`: Read -> All
4. **Assign Role:** Assign this role to the API User. This ensures the user can see all records without being assigned to specific teams.
5. Copy the **API Key** from the user profile and enter it into the `API Token` field in the Dashboard.

## Zammad

Zammad uses Personal Access Tokens.

### How to configure
1. **Create an API User:** Create a dedicated agent user (e.g., `api-dashboard@example.com`).
2. **Grant Global Access:**
   - Ensure the user has the **Agent** role.
   - Under **Permissions**, ensure the user has **Read** access to all relevant **Groups**. In Zammad, an agent needs explicit access to groups to see their tasks.
3. **Log in as the API User**.
4. Click on the avatar > **Profile** > **Token Access**.
5. Click **Create** with the following **permissions**:
   - `ticket.agent`: Allows reading tasks and users.
6. Copy the token and enter it into the `API Token` field in the Dashboard configuration.

## OpenProject

OpenProject uses API Keys associated with a user account.

### Recommended: Global View Permissions
1. **Create an API User:** Create a dedicated user (e.g., `api-dashboard`).
2. **Global Access:**
   - Add the user to projects as a "Viewer".
   - Alternatively, if you have many projects, you can use a "Global Role" (if supported by your version) or ensure the user is added to a group that has read access to all projects.
3. **Log in as the API User**.
4. Navigate to **My account** > **Access Token**.
5. Find the **API key** row and click **Generate**.
6. In the Dashboard configuration:
   - Enter the API Key into the `API Token` field.
   - Username remains `apikey`.
