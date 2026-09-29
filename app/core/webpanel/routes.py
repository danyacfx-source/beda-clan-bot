"""Регистрация HTTP-маршрутов веб-панели.

Этот модуль намеренно не содержит бизнес-логики: обработчики остаются методами
``WebPanel``, а таблица URL, HTTP-методов и требуемых ролей находится в одном
месте. Это упрощает аудит прав доступа и дальнейшее выделение доменных роутов.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web


def register_routes(panel: Any, app: web.Application) -> None:
    """Подключает публичные и защищённые маршруты панели к ``aiohttp`` app."""
    router = app.router
    router.add_get("/", panel._redirect_index)
    router.add_get("/admin", panel._serve_index)
    router.add_get("/admin/", panel._serve_index)
    router.add_get("/admin/embed-constructor", panel._serve_index)
    router.add_get("/logs", panel._serve_logs_page)
    router.add_get("/audit", panel._serve_audit_page)
    router.add_get("/panel.js", panel._serve_script)
    router.add_post("/api/login", panel._api_login)
    router.add_get("/oauth/discord", panel._oauth_start)
    router.add_get("/oauth/discord/callback", panel._oauth_callback)

    viewer = panel._authorized
    bridge = panel._bridge_or_authorized
    router.add_post("/api/logout", viewer(panel._api_logout, "viewer"))
    router.add_get("/api/session", viewer(panel._api_session))
    router.add_get("/api/status", viewer(panel._api_status))
    router.add_get("/api/health", viewer(panel._api_health))
    router.add_get("/api/metrics", viewer(panel._api_metrics))
    router.add_get("/metrics", viewer(panel._prometheus_metrics))
    router.add_get("/api/overview", viewer(panel._api_overview))
    router.add_get("/api/monitor", viewer(panel._api_monitor))
    router.add_get("/api/stats", viewer(panel._api_stats))
    router.add_get("/api/analytics", viewer(panel._api_analytics))
    router.add_get("/api/analytics/export", viewer(panel._api_analytics_export, "admin"))
    router.add_get("/ws/analytics", panel._ws_analytics)
    router.add_get("/api/server", viewer(panel._api_server))
    router.add_get("/api/server/members", viewer(panel._api_server_members))
    router.add_post("/api/server/members/roles", viewer(panel._api_server_members_roles))
    router.add_get("/api/moderation/warns", viewer(panel._api_warns))
    router.add_post("/api/moderation/warn", viewer(panel._api_warn_add))
    router.add_delete("/api/moderation/warns/{warn_id}", viewer(panel._api_warn_delete))
    router.add_post("/api/moderation/clear", viewer(panel._api_warn_clear))
    router.add_post("/api/moderation/kick", viewer(panel._api_mod_kick))
    router.add_post("/api/moderation/ban", viewer(panel._api_mod_ban))
    router.add_post("/api/moderation/unban", viewer(panel._api_mod_unban))
    router.add_post("/api/moderation/timeout", viewer(panel._api_mod_timeout))
    router.add_get("/api/giveaways", viewer(panel._api_giveaways))
    router.add_post("/api/giveaways/create", viewer(panel._api_giveaway_create))
    router.add_post("/api/giveaways/end", viewer(panel._api_giveaway_end))
    router.add_post("/api/giveaways/reroll", viewer(panel._api_giveaway_reroll))
    router.add_get("/api/tickets", viewer(panel._api_tickets))
    router.add_get("/api/tickets/panel", viewer(panel._api_tickets_panel_get))
    router.add_post("/api/tickets/panel", viewer(panel._api_tickets_panel_post))
    router.add_post("/api/tickets/{ticket_id}/close", viewer(panel._api_ticket_close))
    router.add_get("/api/tickets/{ticket_id}/transcript", viewer(panel._api_ticket_transcript))
    router.add_get("/api/automod", viewer(panel._api_automod_get))
    router.add_post("/api/automod", viewer(panel._api_automod_post))
    router.add_post("/api/automod/lockdown", viewer(panel._api_automod_lockdown, "admin"))
    router.add_get("/api/polls", viewer(panel._api_polls))
    router.add_post("/api/polls/create", viewer(panel._api_polls_create))
    router.add_post("/api/polls/{poll_id}/end", viewer(panel._api_polls_end))
    router.add_get("/api/birthdays", viewer(panel._api_birthdays_get))
    router.add_post("/api/birthdays", viewer(panel._api_birthdays_post))
    router.add_post("/api/birthdays/{user_id}/remove", viewer(panel._api_birthdays_remove))
    router.add_get("/api/tempvoice", bridge(panel._api_tempvoice_get))
    router.add_post("/api/tempvoice/{channel_id}/delete", bridge(panel._api_tempvoice_delete, "admin"))
    router.add_post("/api/tempvoice/{channel_id}/transfer", bridge(panel._api_tempvoice_transfer, "admin"))
    router.add_get("/api/schedule", viewer(panel._api_schedule))
    router.add_post("/api/schedule", viewer(panel._api_schedule_create))
    router.add_delete("/api/schedule/{schedule_id}", viewer(panel._api_schedule_delete))
    router.add_get("/api/backup", viewer(panel._api_backup))
    router.add_get("/api/backup/db", viewer(panel._api_backup_db))
    router.add_get("/api/settings", viewer(panel._api_settings_get))
    router.add_post("/api/settings", viewer(panel._api_settings_post))
    router.add_get("/api/modules", viewer(panel._api_modules_get))
    router.add_post("/api/modules", viewer(panel._api_modules_post))
    router.add_post("/api/modules/reset", viewer(panel._api_modules_reset))
    router.add_post("/api/upload", viewer(panel._api_upload))
    router.add_get("/api/uploads", viewer(panel._api_uploads_list))
    router.add_delete("/api/uploads/{name}", viewer(panel._api_uploads_delete))
    panel._uploads_dir.mkdir(parents=True, exist_ok=True)
    router.add_static("/uploads", str(panel._uploads_dir), show_index=False)
    router.add_get("/api/bot/channels", viewer(panel._api_bot_channels))
    router.add_post("/api/bot/send", viewer(panel._api_bot_send))
    router.add_post("/api/bot/edit", viewer(panel._api_bot_edit))
    router.add_post("/api/bot/fetch", viewer(panel._api_bot_fetch))
    router.add_post("/api/webhook/send", viewer(panel._api_webhook_send))
    router.add_post("/api/webhook/edit", viewer(panel._api_webhook_edit))
    router.add_post("/api/webhook/fetch", viewer(panel._api_webhook_fetch))
    router.add_get("/api/logs", viewer(panel._api_logs))
    router.add_get("/api/admin-audit", viewer(panel._api_admin_audit, "admin"))
