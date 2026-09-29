"""Тесты сервисов сообщества: напоминания, опросы, розыгрыши, варны."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.db.database import Database
from app.db.giveaways_repository import GiveawaysRepository
from app.db.polls_repository import PollsRepository
from app.db.reminders_repository import RemindersRepository
from app.db.warns_repository import WarnsRepository


@pytest.fixture
async def db(tmp_path):
    database = Database(os.path.join(tmp_path, "test.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_reminders_due_up_to(db):
    repo = RemindersRepository(db)
    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(seconds=5)
    rid = await repo.add(100, 1, 2, "Покормить кота", future)
    await repo.add(100, 1, 2, "Уже пора", past)

    due = await repo.due_up_to(datetime.now(UTC))
    assert len(due) == 1
    assert due[0]["message"] == "Уже пора"
    assert len(await repo.active_for_user(100)) == 2

    assert await repo.cancel(100, rid) is True
    assert await repo.cancel(100, rid) is False


@pytest.mark.asyncio
async def test_reminder_claim_is_single_consumer(db):
    repo = RemindersRepository(db)
    past = datetime.now(UTC) - timedelta(seconds=5)
    reminder_id = await repo.add(100, 1, 2, "Один раз", past)

    first = await repo.claim_due(datetime.now(UTC))
    second = await repo.claim_due(datetime.now(UTC))
    assert first is not None and first["id"] == reminder_id
    assert second is None

    await repo.release_claim(reminder_id)
    third = await repo.claim_due(datetime.now(UTC))
    assert third is not None and third["id"] == reminder_id
    await repo.deactivate(reminder_id)


@pytest.mark.asyncio
async def test_polls_votes_and_results(db):
    repo = PollsRepository(db)
    poll_id = await repo.create(1, 2, 3, "Чай или кофе?", ["Чай", "Кофе"])
    assert await repo.cast_vote(poll_id, 10, 0) == 2
    assert await repo.cast_vote(poll_id, 11, 0) == 2
    assert await repo.cast_vote(poll_id, 10, 1) == 1
    assert await repo.cast_vote(poll_id, 10, 1) == 0

    counts = await repo.vote_counts(poll_id)
    assert counts == {0: 1, 1: 1}

    await repo.end(poll_id)
    ended_poll = await repo.get(poll_id)
    assert ended_poll["active"] == 0


@pytest.mark.asyncio
async def test_giveaways_join_draw_and_expire(db):
    repo = GiveawaysRepository(db)
    ends_at = datetime.now(UTC) + timedelta(minutes=5)
    gid = await repo.create(1, 2, 3, "Nitro", 2, ends_at)
    for uid in (1, 2, 3, 4, 5):
        assert await repo.add_entry(gid, uid) is True
    assert await repo.add_entry(gid, 1) is False  # повторное участие

    entries = await repo.entries(gid)
    assert len(entries) == 5

    from app.services.giveaway_service import GiveawayService

    service = GiveawayService(repo)
    winners = service.draw(entries, 2)
    assert len(winners) == 2 and set(winners) <= set(entries)
    all_winners = service.draw(entries, 99)
    assert len(all_winners) == len(entries)
    assert len(all_winners) == len(set(all_winners))

    expired = await repo.active_expired(datetime.now(UTC) + timedelta(minutes=10))
    assert len(expired) == 1
    await repo.end(gid)
    assert await repo.active_expired(datetime.now(UTC) + timedelta(minutes=10)) == []


@pytest.mark.asyncio
async def test_warns_delete_single(db):
    repo = WarnsRepository(db)
    first = await repo.add(1, 50, 2, "Спам", "2026-01-01T00:00:00+00:00")
    await repo.add(1, 50, 2, "Флуд", "2026-01-02T00:00:00+00:00")

    assert await repo.count_for_user(1, 50) == 2
    assert await repo.delete(1, first) is True
    assert await repo.delete(1, first) is False
    assert await repo.count_for_user(1, 50) == 1
