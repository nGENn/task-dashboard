from http import HTTPStatus

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from task_dashboard.users.models import ServiceConfiguration


class Command(BaseCommand):
    help = "Robust Zammad Seeder (No Elasticsearch required)"

    def handle(self, *args, **options):
        # Ensure configuration exists
        config, _ = ServiceConfiguration.objects.get_or_create(
            service_type="zammad",
            defaults={
                "name": "Zammad",
                "api_url": getattr(settings, "ZAMMAD_API_URL", ""),
                "api_token": getattr(settings, "ZAMMAD_API_TOKEN", ""),
                "is_active": True,
            },
        )
        base_url = config.api_url.rstrip("/")
        token = config.api_token
        headers = {
            "Authorization": f"Token token={token}",
            "Content-Type": "application/json",
        }
        verify_ssl = getattr(settings, "ZAMMAD_VERIFY_SSL", True)
        client = httpx.Client(headers=headers, verify=verify_ssl, timeout=10)

        self.stdout.write("🌱 Seeding Zammad...")

        # 1. Ensure Group "Support" Exists
        group_name = "Support"
        try:
            groups = client.get(f"{base_url}/api/v1/groups").json()
            group = next((g for g in groups if g["name"] == group_name), None)

            if not group:
                self.stdout.write(f"  - Creating Group: {group_name}")
                resp = client.post(
                    f"{base_url}/api/v1/groups",
                    json={"name": group_name, "active": True},
                )
                resp.raise_for_status()
                group_id = resp.json()["id"]
            else:
                group_id = group["id"]
                self.stdout.write(f"  - Using Group: {group_name} (ID: {group_id})")
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            self.stdout.write(self.style.ERROR(f"Failed to setup group: {e}"))
            return

        # 2. Priorities Map
        priorities = client.get(f"{base_url}/api/v1/ticket_priorities").json()
        prio_map = {p["name"].lower(): p["id"] for p in priorities}

        # 3. Ensure Users & Permissions
        def ensure_user(email, firstname, lastname, role_name):
            # List users (Search API is broken without ES)
            # In production, use search; for local dev with <100 users, list is fine.
            users = client.get(f"{base_url}/api/v1/users").json()
            user = next((u for u in users if u["email"] == email), None)

            # Find Role ID
            roles = client.get(f"{base_url}/api/v1/roles").json()
            role_id = next((r["id"] for r in roles if r["name"] == role_name), None)

            payload = {
                "login": email,
                "email": email,
                "firstname": firstname,
                "lastname": lastname,
                "role_ids": [role_id]
                if role_id
                else [2],  # Default to Agent if not found
                "password": "password123",
                # CRITICAL: Give access to the group so they can be assigned tasks!
                "group_ids": {str(group_id): ["full"]},
            }

            if not user:
                resp = client.post(f"{base_url}/api/v1/users", json=payload)
                if resp.status_code == HTTPStatus.CREATED:
                    return resp.json()["id"]
            else:
                # Update existing user to ensure they have group permissions
                client.put(f"{base_url}/api/v1/users/{user['id']}", json=payload)
                return user["id"]
            return 1  # Fallback to Admin

        cust_id = ensure_user("customer@demo.local", "Alice", "Customer", "Customer")
        agent_id = ensure_user("agent@demo.local", "Bob", "Agent", "Agent")

        # 4. Create Tasks (Check Duplicates via List, not Search)
        tasks = [
            {
                "title": "Printer on fire",
                "state": "new",
                "prio": "3 high",
                "owner_id": None,
            },
            {
                "title": "VPN access request",
                "state": "open",
                "prio": "2 normal",
                "owner_id": agent_id,
            },
            {
                "title": "Password reset needed",
                "state": "closed",
                "prio": "1 low",
                "owner_id": agent_id,
            },
            {
                "title": "Server API 500 Error",
                "state": "open",
                "prio": "3 high",
                "owner_id": agent_id,
            },
        ]

        # Fetch all existing tasks to check titles
        existing_tasks = client.get(f"{base_url}/api/v1/tickets").json()
        existing_titles = {t["title"] for t in existing_tasks}

        for t in tasks:
            if t["title"] in existing_titles:
                self.stdout.write(f"  - Skipped (Exists): {t['title']}")
                continue

            payload = {
                "title": t["title"],
                "group_id": group_id,
                "customer_id": cust_id,
                "owner_id": t["owner_id"],
                "priority_id": prio_map.get(t["prio"], 2),
                "state": t["state"],
                "article": {
                    "subject": t["title"],
                    "body": "Seeded task body.",
                    "type": "note",
                    "internal": False,
                },
            }

            resp = client.post(f"{base_url}/api/v1/tickets", json=payload)
            if resp.status_code == HTTPStatus.CREATED:
                self.stdout.write(self.style.SUCCESS(f"  + Created: {t['title']}"))
            else:
                self.stdout.write(self.style.ERROR(f"  x Failed: {resp.text}"))

        self.stdout.write("✅ Zammad seeding complete.")
