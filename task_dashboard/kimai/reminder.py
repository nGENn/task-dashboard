"""
Pure, I/O-free reminder evaluator (V4).

`calc_days_behind` is the only public function.
All inputs are plain Python values — no DB/cache/network calls here.
"""

import logging
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta

logger = logging.getLogger(__name__)

# German weekday abbreviation → Python weekday() int (Monday=0) (V15)
_DE_WEEKDAY: dict[str, int] = {
    "Mo": 0,
    "Di": 1,
    "Mi": 2,
    "Do": 3,
    "Fr": 4,
    "Sa": 5,
    "So": 6,
}


def parse_working_days(account_number: str | None) -> frozenset[int]:
    """
    Parse Kimai user.accountNumber field into a set of weekday ints.

    Empty/None → defaults to Mon–Fri (0–4). Invalid tokens logged and skipped (V15).
    """
    if not account_number or not account_number.strip():
        return frozenset({0, 1, 2, 3, 4})

    result = set()
    for raw_token in account_number.split(","):
        token = raw_token.strip()
        if not token:
            continue
        day_int = _DE_WEEKDAY.get(token)
        if day_int is None:
            logger.warning("Unknown Kimai weekday abbreviation: %r", token)
        else:
            result.add(day_int)
    return frozenset(result)


def calc_days_behind(
    last_entry_end: datetime | None,
    working_days: frozenset[int],
    holidays: frozenset[date],
    today: date | None = None,
) -> int:
    """
    Return the number of business days the user is behind on time tracking (V4).

    A "business day" is any day that:
    - Is in `working_days` (user's configured weekdays)
    - Is not in `holidays`
    - Is strictly before `today`

    If `last_entry_end` is None the user has never logged time → returns 0
    (caller decides how to handle this case).

    If `working_days` is empty → returns 0 (V7).
    """
    if not working_days:
        return 0

    if today is None:
        today = datetime.now(tz=UTC).date()

    if last_entry_end is None:
        return 0

    # Normalise to date
    if hasattr(last_entry_end, "date"):
        last_date = last_entry_end.date()
    else:
        last_date = last_entry_end

    # Count business days strictly between last_date (exclusive) and today (exclusive)
    count = 0
    current = last_date
    while True:
        current = current + timedelta(days=1)
        if current >= today:
            break
        if current.weekday() in working_days and current not in holidays:
            count += 1

    return count
