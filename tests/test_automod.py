"""Тесты авто-модерации."""
from app.cogs.moderation.automod import _is_caps


class TestCapsDetector:
    def test_all_caps_long(self):
        assert _is_caps("ЭТО СООБЩЕНИЕ НАПИСАНО ВЕРХНИМ РЕГИСТРОМ КРИКОМ") is True

    def test_normal_text(self):
        assert _is_caps("Это обычное сообщение в нормальном регистре") is False

    def test_short(self):
        assert _is_caps("ОК") is False

    def test_mixed(self):
        assert _is_caps("This is A Mixed Case Message With Some Uppercase Letters Here") is False
