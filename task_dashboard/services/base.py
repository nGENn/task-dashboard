import logging
from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache

logger = logging.getLogger(__name__)


class BaseService(ABC):
    STATUS_MAPPING: dict[str, list[str]] = {}
    PRIORITY_MAPPING: dict[str, list[str]] = {}

    def __init__(self, config):
        self.config = config

    def get_tasks(self, *, force_refresh=False):
        return async_to_sync(self.get_tasks_async)(force_refresh=force_refresh)

    @abstractmethod
    async def get_tasks_async(self, *, force_refresh=False) -> list[dict]: ...

    def get_single_task(self, task) -> dict | None:
        return async_to_sync(self.get_single_task_async)(task)

    @abstractmethod
    async def get_single_task_async(self, task) -> dict | None: ...

    @abstractmethod
    def check_health(self) -> dict[str, Any]: ...

    def _map_status(self, state_name: str | None) -> str:
        s = str(state_name).lower()
        if any(x in s for x in self.STATUS_MAPPING.get("open", [])):
            return "open"
        if any(x in s for x in self.STATUS_MAPPING.get("closed", [])):
            return "closed"
        return "pending"

    def _map_priority(self, prio_name: str | None) -> str:
        p = str(prio_name).lower()
        if any(x in p for x in self.PRIORITY_MAPPING.get("Critical", [])):
            return "Critical"
        if any(x in p for x in self.PRIORITY_MAPPING.get("High", [])):
            return "High"
        if any(x in p for x in self.PRIORITY_MAPPING.get("Low", [])):
            return "Low"
        return "Medium"

    def _format_date(self, date_str: str | None) -> str:
        """Returns an ISO 8601 string suitable for duration calculations."""
        if not date_str:
            return ""
        try:
            dt_str = str(date_str).replace("Z", "+00:00")
            datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return str(date_str)
        else:
            return dt_str

    async def _fetch_and_cache(
        self, cache_key: str, timeout_secs: int, fetch_fn: Callable, error_msg: str
    ) -> dict:
        cached = await cache.aget(cache_key)
        if cached is not None:
            return cached

        data = {}
        try:
            data = await fetch_fn()
            await cache.aset(cache_key, data, timeout=timeout_secs)
        except httpx.HTTPError as e:
            logger.warning("%s: %s", error_msg, e)
        except Exception:
            logger.exception("Unexpected error in %s", error_msg)
        return data
