import logging
from datetime import date

import httpx
from django.core.cache import cache

logger = logging.getLogger(__name__)

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
CACHE_TTL = 86400  # 24h


def get_public_holidays(country: str, year: int) -> frozenset[date]:
    """
    Fetch public holidays from nager.at for the given country+year.
    Fail-open: returns cached value or empty set on error (V14).
    """
    cache_key = f"kimai_holidays:{country}:{year}"
    cached = cache.get(cache_key)
    if cached is not None:
        return frozenset(date.fromisoformat(d) for d in cached)

    try:
        url = NAGER_URL.format(year=year, country=country)
        with httpx.Client(timeout=10.0) as c:
            resp = c.get(url)
            resp.raise_for_status()
            data = resp.json()

        date_strings = [item["date"] for item in data if "date" in item]
        cache.set(cache_key, date_strings, timeout=CACHE_TTL)
        return frozenset(date.fromisoformat(d) for d in date_strings)

    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to fetch public holidays for %s/%s", country, year, exc_info=True
        )
        return frozenset()
