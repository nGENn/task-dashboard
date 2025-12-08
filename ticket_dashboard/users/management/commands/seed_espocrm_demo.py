import httpx
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Simple EspoCRM Seeder"

    def handle(self, *args, **options):
        base_url = settings.ESPO_API_URL.rstrip("/")
        api_key = settings.ESPO_API_KEY
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
        client = httpx.Client(headers=headers, verify=False, timeout=10)

        self.stdout.write("🌱 Seeding EspoCRM...")

        # 1. Find User ID (Admin)
        try:
            resp = client.get(f"{base_url}/api/v1/User?maxSize=1")
            resp.raise_for_status()  # Raise error if 403 Forbidden
            users = resp.json()["list"]
            user_id = users[0]["id"]
        except Exception as e:
            # Print the actual error from the server
            self.stdout.write(self.style.ERROR(f"Espo Error: {e}"))
            if "resp" in locals():
                self.stdout.write(f"Response: {resp.text}")
            return

        # 2. Find or Create Account (Customer)
        account_name = "Acme Corp"
        acc_search = client.get(
            f"{base_url}/api/v1/Account?where[0][type]=equals&where[0][attribute]=name&where[0][value]={account_name}"
        ).json()

        if acc_search["total"] > 0:
            account_id = acc_search["list"][0]["id"]
        else:
            resp = client.post(
                f"{base_url}/api/v1/Account", json={"name": account_name}
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
                f"{base_url}/api/v1/Case?where[0][type]=equals&where[0][attribute]=name&where[0][value]={c['name']}"
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
                f"{base_url}/api/v1/Task?where[0][type]=equals&where[0][attribute]=name&where[0][value]={t['name']}"
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
