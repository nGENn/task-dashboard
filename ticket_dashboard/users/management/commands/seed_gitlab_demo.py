from http import HTTPStatus

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from ticket_dashboard.users.models import ServiceConfiguration


class Command(BaseCommand):
    help = "Simple GitLab Seeder"

    def handle(self, *args, **options):
        # Ensure configuration exists
        config, _ = ServiceConfiguration.objects.get_or_create(
            service_type="gitlab",
            defaults={
                "name": "GitLab",
                "api_url": getattr(settings, "GITLAB_API_URL", "https://gitlab.com"),
                "api_token": getattr(settings, "GITLAB_API_TOKEN", ""),
                "is_active": True,
            },
        )
        base_url = (config.api_url or "https://gitlab.com").rstrip("/")
        token = config.api_token
        headers = {"Private-Token": token}
        verify_ssl = getattr(settings, "GITLAB_VERIFY_SSL", True)
        client = httpx.Client(headers=headers, verify=verify_ssl, timeout=10)

        self.stdout.write("🌱 Seeding GitLab...")

        # 1. Get or Create Project
        project_name = "Dashboard Demo"
        # Search for project owned by user
        projs = client.get(
            f"{base_url}/api/v4/projects?search={project_name}&membership=true",
        ).json()

        if projs:
            pid = projs[0]["id"]
            self.stdout.write(f"  - Using existing project ID: {pid}")
        else:
            resp = client.post(
                f"{base_url}/api/v4/projects",
                json={"name": project_name, "visibility": "private"},
            )
            if resp.status_code != HTTPStatus.CREATED:
                self.stdout.write(
                    self.style.ERROR(f"Failed to create project: {resp.text}"),
                )
                return
            pid = resp.json()["id"]
            self.stdout.write(f"  - Created project ID: {pid}")

        # 2. Create Issues
        issues = [
            {"title": "Fix CSS on Login Page", "labels": "High,Bug"},
            {"title": "Update Documentation", "labels": "Low,Docs"},
            {"title": "Refactor User Model", "labels": "Critical,Backend"},
        ]

        for i in issues:
            # Idempotency check
            check = client.get(
                f"{base_url}/api/v4/projects/{pid}/issues?search={i['title']}",
            ).json()
            if check:
                continue

            client.post(
                f"{base_url}/api/v4/projects/{pid}/issues",
                json={"title": i["title"], "labels": i["labels"]},
            )
            self.stdout.write(self.style.SUCCESS(f"  + Issue: {i['title']}"))

        # 3. Create Merge Requests
        # MRs require a branch. For simplicity, we just check if any exist, if not create one dummy one if possible.  # noqa: E501  # noqa: E501
        # Creating MRs via API from scratch requires creating branches first which is complex.  # noqa: E501  # noqa: E501
        # We will skip MR creation logic here to keep it simple unless you specifically need it.  # noqa: E501  # noqa: E501
        # If you need it, simply creating Issues (as above) is usually enough to populate the dashboard.  # noqa: E501  # noqa: E501

        self.stdout.write("✅ GitLab seeding complete.")
