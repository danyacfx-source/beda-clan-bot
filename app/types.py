"""Typed row contracts shared by repositories and services.

SQLite returns dynamically shaped rows. Keeping the public contracts here makes
the service layer explicit without pretending that a raw ``aiosqlite.Row`` is
already type-safe.
"""

from __future__ import annotations

from typing import TypedDict


class ReminderRow(TypedDict):
    id: int
    user_id: int
    guild_id: int | None
    channel_id: int | None
    message: str
    remind_at: str


class ReminderSummary(TypedDict):
    id: int
    message: str
    remind_at: str


class ScheduledMessageRow(TypedDict):
    id: int
    guild_id: int
    channel_id: int
    author_id: int
    content: str
    embed_json: str
    send_at: str
    created_at: str
    done: int
    processing_until: str | None


class GiveawayRow(TypedDict):
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    author_id: int
    prize: str
    winners: int
    ends_at: str
    created_at: str
    active: int
    min_days: int
    processing_until: str | None


class ModerationCaseRow(TypedDict):
    case_id: int
    guild_id: int
    user_id: int
    moderator_id: int
    action: str
    reason: str
    created_at: str
    expires_at: str | None
    active: int


class WarnRow(TypedDict):
    id: int
    user_id: int
    moderator_id: int
    reason: str
    created_at: str


class TicketRow(TypedDict):
    ticket_id: int
    guild_id: int
    channel_id: int
    creator_id: int
    status: str
    created_at: str
    closed_at: str | None
    transcript: str | None
    voice_channel_id: int | None


class BirthdayRow(TypedDict):
    user_id: int
    month: int
    day: int


class TempVoiceRow(TypedDict):
    owner_id: int
    channel_id: int
    created_at: str


class EventRow(TypedDict):
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    name: str
    event_type: str
    description: str
    briefing_at: str
    start_at: str
    image_url: str
    show_not_going: int
    creator_id: int
    created_at: str
    active: int
    reminded_briefing: int
    reminded_start: int


class EventSignupRow(TypedDict):
    user_id: int
    role: str


class WherePlayRow(TypedDict):
    guild_id: int
    channel_id: int
    role_id: int | None
    message_id: int | None
    code: str
    team: str
    caller_id: int | None
    active: int
    payload: str
    fetched_at: str


class CallerRoomRow(TypedDict):
    guild_id: int
    member_id: int
    channel_id: int
    updated_at: str


class DossierSettingsRow(TypedDict):
    guild_id: int
    forum_id: int
    roles: str


class AdmissionRow(TypedDict):
    guild_id: int
    owner_id: int
    ticket_id: int


class DossierDraftRow(TypedDict):
    guild_id: int
    owner_id: int
    ticket_id: int
    data: str
    revision: int


class DossierDraftTicketRow(TypedDict):
    owner_id: int
    data: str
    revision: int


class DossierRow(TypedDict):
    guild_id: int
    owner_id: int
    data: str
    thread_id: int | None
    status: str
