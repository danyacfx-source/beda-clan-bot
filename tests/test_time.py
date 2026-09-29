"""Тесты утилит времени."""


from app.utils.time import format_duration, parse_duration


class TestParseDuration:
    def test_simple_units(self):
        assert parse_duration("30s") == 30
        assert parse_duration("2m") == 120
        assert parse_duration("2h") == 7200
        assert parse_duration("1d") == 86400

    def test_combined(self):
        assert parse_duration("1h 30m") == 5400
        assert parse_duration("2m30s") == 150

    def test_invalid(self):
        assert parse_duration("banana") is None
        assert parse_duration("") is None
        assert parse_duration("0") is None

    def test_case_insensitive(self):
        assert parse_duration("5S") == 5
        assert parse_duration("1H") == 3600


class TestFormatDuration:
    def test_minutes(self):
        assert format_duration(150) == "2:30"

    def test_hours(self):
        assert format_duration(3600 + 61) == "1:01:01"

    def test_none_live(self):
        assert format_duration(None) == "live"
        assert format_duration(0) == "live"
