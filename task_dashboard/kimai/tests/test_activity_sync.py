"""Tests for activity sync helpers (V3, V12)."""

from task_dashboard.kimai.tasks import _activity_comment
from task_dashboard.kimai.tasks import _parse_activity_comment


class TestActivityCommentFormat:
    """V3: activity.comment = "{service_config_id}:{external_task_id}" """

    def test_format(self):
        assert _activity_comment(42, "ZAM-123") == "42:ZAM-123"

    def test_format_numeric_external(self):
        assert _activity_comment(1, "456") == "1:456"

    def test_parse_valid(self):
        assert _parse_activity_comment("42:ZAM-123") == (42, "ZAM-123")

    def test_parse_task_id_with_colon(self):
        # External task IDs with colons: only first separator is split
        assert _parse_activity_comment("5:GL-I-10:extra") == (5, "GL-I-10:extra")

    def test_parse_none(self):
        assert _parse_activity_comment(None) is None

    def test_parse_empty(self):
        assert _parse_activity_comment("") is None

    def test_parse_no_separator(self):
        assert _parse_activity_comment("no-colon") is None

    def test_parse_non_numeric_config_id(self):
        assert _parse_activity_comment("abc:task-1") is None

    def test_roundtrip(self):
        config_id, ext_id = 7, "OP-99"
        parsed = _parse_activity_comment(_activity_comment(config_id, ext_id))
        assert parsed == (config_id, ext_id)

    def test_different_config_ids_are_distinct(self):
        """V3: comment encodes config_id so no cross-service collision."""
        c1 = _activity_comment(1, "TASK-1")
        c2 = _activity_comment(2, "TASK-1")
        assert c1 != c2
        assert _parse_activity_comment(c1) == (1, "TASK-1")
        assert _parse_activity_comment(c2) == (2, "TASK-1")
