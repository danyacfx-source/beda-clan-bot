"""Тесты форматирования текста."""
from app.utils.format import plural, truncate


class TestTruncate:
    def test_short_unchanged(self):
        assert truncate("привет") == "привет"

    def test_long_truncated(self):
        result = truncate("a" * 50, limit=10)
        assert len(result) <= 10
        assert result.endswith("...")

    def test_exact_limit(self):
        assert truncate("abcde", limit=5) == "abcde"


class TestPlural:
    def test_russian_plural(self):
        assert plural(1, "сообщение", "сообщения", "сообщений") == "сообщение"
        assert plural(2, "сообщение", "сообщения", "сообщений") == "сообщения"
        assert plural(5, "сообщение", "сообщения", "сообщений") == "сообщений"
        assert plural(21, "сообщение", "сообщения", "сообщений") == "сообщение"
        assert plural(11, "сообщение", "сообщения", "сообщений") == "сообщений"
