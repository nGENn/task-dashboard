"""
Pure, I/O-free reminder evaluator.

`calc_days_behind` is the only public function.
All inputs are plain Python values — no DB/cache/network calls here.
"""

from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta


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
