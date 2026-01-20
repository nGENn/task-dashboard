import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from ticket_dashboard.users.models import ServiceConfiguration


class Command(BaseCommand):
    help = "Simple EspoCRM Seeder"

    def handle(self, *args, **options):
        # Ensure configuration exists
        config, _ = ServiceConfiguration.objects.get_or_create(
            service_type="espocrm",
            defaults={
                "name": "EspoCRM",
                "api_url": getattr(settings, "ESPO_API_URL", ""),
                "api_token": getattr(settings, "ESPO_API_KEY", ""),
                "is_active": True,
            },
        )
        base_url = config.api_url.rstrip("/")
        api_key = config.api_token
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
        verify_ssl = getattr(settings, "ESPO_VERIFY_SSL", True)
        client = httpx.Client(headers=headers, verify=verify_ssl, timeout=10)

        self.stdout.write("🌱 Seeding EspoCRM...")

        # 1. Find User ID (Admin)
        try:
            resp = client.get(f"{base_url}/api/v1/User?maxSize=1")
            resp.raise_for_status()
            users = resp.json()["list"]
            user_id = users[0]["id"]
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            # Print the actual error from the server
            self.stdout.write(self.style.ERROR(f"Espo Error: {e}"))
            if "resp" in locals():
                self.stdout.write(f"Response: {resp.text}")
            return

        # 2. Find or Create Account (Customer)
        account_name = "Acme Corp"
        acc_search = client.get(
            f"{base_url}/api/v1/Account?where[0][type]=equals&where[0][attribute]=name&where[0][value]={account_name}",
        ).json()

        if acc_search["total"] > 0:
            account_id = acc_search["list"][0]["id"]
        else:
            resp = client.post(
                f"{base_url}/api/v1/Account",
                json={"name": account_name},
            )
            account_id = resp.json()["id"]

        # 3. Create Cases
        cases = [
            {
                "name": "Contract Renegotiation",
                "status": "Assigned",
                "priority": "High",
            },
            {"name": "Q3 Sales Review", "status": "New", "priority": "Normal"},
            {"name": "Onboard New Partner", "status": "Closed", "priority": "Low"},
        ]

        for c in cases:
            # Check existence
            check = client.get(
                f"{base_url}/api/v1/Case?where[0][type]=equals&where[0][attribute]=name&where[0][value]={c['name']}",
            ).json()
            if check["total"] > 0:
                continue

            payload = {
                "name": c["name"],
                "status": c["status"],
                "priority": c["priority"],
                "accountId": account_id,
                "assignedUserId": user_id,
            }
            client.post(f"{base_url}/api/v1/Case", json=payload)
            self.stdout.write(self.style.SUCCESS(f"  + Case: {c['name']}"))

        # 4. Create Tasks
        tasks = [
            {"name": "Call CEO", "status": "Not Started"},
            {"name": "Draft Proposal", "status": "In Progress"},
        ]

        for t in tasks:
            check = client.get(
                f"{base_url}/api/v1/Task?where[0][type]=equals&where[0][attribute]=name&where[0][value]={t['name']}",
            ).json()
            if check["total"] > 0:
                continue

            payload = {
                "name": t["name"],
                "status": t["status"],
                "assignedUserId": user_id,
                "accountId": account_id,
            }
            client.post(f"{base_url}/api/v1/Task", json=payload)
            self.stdout.write(self.style.SUCCESS(f"  + Task: {t['name']}"))

        self.stdout.write("✅ EspoCRM seeding complete.")

