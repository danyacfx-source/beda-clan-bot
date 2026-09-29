"""Регрессионные тесты интерактивных представлений."""

from app.core.views import ConfirmView


async def test_confirm_view_disables_all_controls_without_nonexistent_api() -> None:
    view = ConfirmView()

    view._disable_all_items()

    assert view.children
    assert all(getattr(item, "disabled", False) for item in view.children)
