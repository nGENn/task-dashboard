import logging
import time
from typing import Any

import httpx
from asgiref.sync import async_to_sync

from task_dashboard.services.base import BaseService

from .client import KimaiClient

logger = logging.getLogger(__name__)


class KimaiService(BaseService):
    """
    Kimai is not a task source — get_tasks_async returns [] (V18).
    Its only role in the BaseService protocol is the health check.
    """

    STATUS_MAPPING: dict[str, list[str]] = {}
    PRIORITY_MAPPING: dict[str, list[str]] = {}

    def __init__(self, config) -> None:
        self.config = config
        self._client = KimaiClient(
            base_url=config.api_url,
            api_token=config.api_token or "",
        )

    async def get_tasks_async(self, *, force_refresh: bool = False) -> list[dict]:
        return []

    async def get_single_task_async(self, task) -> dict | None:
        return None

    def check_health(self) -> dict[str, Any]:
        start = time.monotonic()
        try:
            async_to_sync(self._client.ping)()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return {"status": "auth_error", "name": self.config.name, "latency": 0}
            return {
                "status": "offline",
                "name": self.config.name,
                "latency": 0,
                "error": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "offline",
                "name": self.config.name,
                "latency": 0,
                "error": str(exc),
            }
        else:
            latency = int((time.monotonic() - start) * 1000)
            return {"status": "online", "name": self.config.name, "latency": latency}
