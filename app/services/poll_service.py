"""Сервис опросов."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import discord

from app.core.base import BaseService
from app.db.polls_repository import PollsRepository

if TYPE_CHECKING:
    from app.db.polls_repository import PollsRepository


class PollService(BaseService[PollsRepository]):
    repo: PollsRepository

    def __init__(self, repo: PollsRepository) -> None:
        super().__init__(repo)

    async def create(self, guild_id: int, channel_id: int, author_id: int, question: str, options: list[str]) -> int:
        return await self._repo.create(guild_id, channel_id, author_id, question, options)

    async def bind_message(self, poll_id: int, message_id: int) -> None:
        await self._repo.set_message_id(poll_id, message_id)

    async def get(self, poll_id: int) -> dict[str, Any] | None:
        return await self._repo.get(poll_id)

    async def vote(self, poll_id: int, user_id: int, option: int, max_options: int) -> int:
        if not 0 <= option < max_options:
            return -1
        poll = await self._repo.get(poll_id)
        if poll is None or not poll["active"]:
            return -1
        return await self._repo.cast_vote(poll_id, user_id, option)

    async def result(self, poll_id: int) -> tuple[str, list[str], dict[int, int]]:
        poll = await self._repo.get(poll_id)
        if poll is None:
            raise ValueError("Опрос не найден")
        options = json.loads(poll["options"])
        counts = await self._repo.vote_counts(poll_id)
        return poll["question"], options, counts

    async def end(self, poll_id: int) -> tuple[str, list[str], dict[int, int]]:
        result = await self.result(poll_id)
        await self._repo.end(poll_id)
        return result

    async def active_with_message(self) -> list[dict[str, Any]]:
        return await self._repo.active_with_message()

    async def list_for_guild(self, guild_id: int, limit: int = 200) -> list[dict[str, Any]]:
        return await self._repo.list_for_guild(guild_id, limit)

    async def vote_counts(self, poll_id: int) -> dict[int, int]:
        return await self._repo.vote_counts(poll_id)

    async def embed(self, poll_id: int) -> discord.Embed:
        from app.core import embeds

        poll = await self._repo.get(poll_id)
        if poll is None:
            raise ValueError("Опрос не найден")
        options = json.loads(poll["options"])
        counts = await self._repo.vote_counts(poll_id)
        total = sum(counts.values())
        embed = embeds.brand("Опрос", f"## {poll['question']}\nВыберите один вариант кнопкой ниже.")
        for index, option in enumerate(options):
            votes = counts.get(index, 0)
            share = round(votes / total * 100) if total else 0
            bar = "▰" * round(share / 10) + "▱" * (10 - round(share / 10))
            embed.add_field(
                name=f"{_NUM_REACTIONS[index]}  {option}",
                value=f"`{bar}`  **{share}%** · {votes} голосов",
                inline=False,
            )
        embed.set_footer(text=f"Асуна Юки  •  Опрос #{poll_id}  •  {total} голосов")
        return embed


_NUM_REACTIONS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
