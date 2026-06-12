import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 30.0
BULK_CONCURRENCY = 10


class KimaiClient:
    def __init__(self, base_url: str, api_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=TIMEOUT)

    async def get_users(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.get(f"{self.base_url}/api/users")
            resp.raise_for_status()
            return resp.json()

    async def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(f"{self.base_url}/api/users", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_last_timesheet(self, user_id: int) -> dict[str, Any] | None:
        async with self._client() as c:
            resp = await c.get(
                f"{self.base_url}/api/timesheets",
                params={"user": user_id, "size": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            return data[0] if data else None

    async def get_last_timesheets_bulk(
        self, user_ids: list[int]
    ) -> dict[int, dict[str, Any] | None]:
        """Fetch each user's last timesheet concurrently.

        Uses a single shared AsyncClient and a semaphore to cap concurrency.
        Returns {user_id: entry|None}.
        """
        sem = asyncio.Semaphore(BULK_CONCURRENCY)

        async def _fetch_one(
            client: httpx.AsyncClient, uid: int
        ) -> tuple[int, dict[str, Any] | None]:
            async with sem:
                try:
                    resp = await client.get(
                        f"{self.base_url}/api/timesheets",
                        params={"user": uid, "size": 1},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    return uid, data[0] if data else None
                except Exception:  # noqa: BLE001
                    return uid, None

        async with self._client() as c:
            results = await asyncio.gather(*(_fetch_one(c, uid) for uid in user_ids))
        return dict(results)

    async def get_projects(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.get(f"{self.base_url}/api/projects")
            resp.raise_for_status()
            return resp.json()

    async def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(f"{self.base_url}/api/projects", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def patch_project(
        self, project_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.patch(
                f"{self.base_url}/api/projects/{project_id}", json=payload
            )
            resp.raise_for_status()
            return resp.json()

    async def get_activities(self, project_id: int) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.get(
                f"{self.base_url}/api/activities",
                params={"project": project_id, "visible": 3},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_activity(self, activity_id: int) -> dict[str, Any]:
        """Fetch a single activity's detail (includes its assigned ``teams``)."""
        async with self._client() as c:
            resp = await c.get(f"{self.base_url}/api/activities/{activity_id}")
            resp.raise_for_status()
            return resp.json()

    async def create_activity(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(f"{self.base_url}/api/activities", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def patch_activity(
        self, activity_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.patch(
                f"{self.base_url}/api/activities/{activity_id}", json=payload
            )
            resp.raise_for_status()
            return resp.json()

    async def get_customers(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.get(f"{self.base_url}/api/customers")
            resp.raise_for_status()
            return resp.json()

    async def create_customer(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(f"{self.base_url}/api/customers", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def get_teams(self) -> list[dict[str, Any]]:
        async with self._client() as c:
            resp = await c.get(f"{self.base_url}/api/teams")
            resp.raise_for_status()
            return resp.json()

    async def create_team(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(f"{self.base_url}/api/teams", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def patch_team(self, team_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.patch(f"{self.base_url}/api/teams/{team_id}", json=payload)
            resp.raise_for_status()
            return resp.json()

    async def add_team_member(self, team_id: int, user_id: int) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(
                f"{self.base_url}/api/teams/{team_id}/members/{user_id}"
            )
            resp.raise_for_status()
            return resp.json()

    async def grant_team_activity(
        self, team_id: int, activity_id: int
    ) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(
                f"{self.base_url}/api/teams/{team_id}/activities/{activity_id}"
            )
            resp.raise_for_status()
            return resp.json()

    async def revoke_team_activity(
        self, team_id: int, activity_id: int
    ) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.delete(
                f"{self.base_url}/api/teams/{team_id}/activities/{activity_id}"
            )
            resp.raise_for_status()
            return resp.json()

    async def grant_team_project(self, team_id: int, project_id: int) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.post(
                f"{self.base_url}/api/teams/{team_id}/projects/{project_id}"
            )
            resp.raise_for_status()
            return resp.json()

    async def ping(self) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.get(f"{self.base_url}/api/version")
            resp.raise_for_status()
            return resp.json()
