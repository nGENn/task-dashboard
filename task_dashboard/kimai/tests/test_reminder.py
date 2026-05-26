"""Tests for the pure reminder evaluator."""

from datetime import UTC
from datetime import date
from datetime import datetime

from task_dashboard.kimai.reminder import calc_days_behind


class TestCalcDaysBehind:
    _WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Mon-Fri

    def _dt(self, iso: str) -> datetime:
        return datetime.fromisoformat(iso).replace(tzinfo=UTC)

    def test_zero_when_last_entry_today(self):
        today = date(2025, 5, 15)  # Thursday
        last = self._dt("2025-05-15T10:00:00")
        assert calc_days_behind(last, self._WEEKDAYS, frozenset(), today) == 0

    def test_zero_when_last_entry_yesterday(self):
        today = date(2025, 5, 15)  # Thursday
        last = self._dt("2025-05-14T10:00:00")  # Wednesday
        assert calc_days_behind(last, self._WEEKDAYS, frozenset(), today) == 0

    def test_one_day_behind(self):
        today = date(2025, 5, 15)  # Thursday
        last = self._dt("2025-05-13T17:00:00")  # Tuesday
        # Wednesday 14th is the only business day between
        assert calc_days_behind(last, self._WEEKDAYS, frozenset(), today) == 1

    def test_weekend_not_counted(self):
        today = date(2025, 5, 19)  # Monday
        last = self._dt("2025-05-15T17:00:00")  # Thursday
        # Fri 16, Mon 19 excluded (today), Sat/Sun excluded → 1 day (Fri)
        assert calc_days_behind(last, self._WEEKDAYS, frozenset(), today) == 1

    def test_holiday_excluded(self):
        today = date(2025, 5, 15)
        last = self._dt("2025-05-12T17:00:00")  # Monday
        holidays = frozenset({date(2025, 5, 13), date(2025, 5, 14)})  # Tue+Wed
        assert calc_days_behind(last, self._WEEKDAYS, holidays, today) == 0

    def test_empty_working_days_returns_zero(self):
        today = date(2025, 5, 15)
        last = self._dt("2025-05-01T17:00:00")
        assert calc_days_behind(last, frozenset(), frozenset(), today) == 0

    def test_none_last_entry_returns_zero(self):
        today = date(2025, 5, 15)
        assert calc_days_behind(None, self._WEEKDAYS, frozenset(), today) == 0

    def test_multiple_days_behind(self):
        today = date(2025, 5, 16)  # Friday
        last = self._dt("2025-05-12T08:00:00")  # Monday
        # Tue 13, Wed 14, Thu 15 = 3 business days
        assert calc_days_behind(last, self._WEEKDAYS, frozenset(), today) == 3  # noqa: PLR2004

    def test_grace_period_comparison(self):
        """Caller compares result to grace_period; test that values are correct."""
        today = date(2025, 5, 16)
        last = self._dt("2025-05-12T08:00:00")
        days = calc_days_behind(last, self._WEEKDAYS, frozenset(), today)
        assert days > 2  # noqa: PLR2004  # trigger with grace_period=2
        assert days <= 3  # noqa: PLR2004  # exact count
