"""Базовые классы архитектуры: ког с типизированными сервисами и сервис поверх репозитория."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from discord.ext import commands

if TYPE_CHECKING:
    from app.core.bot import MegaBot
    from app.db.base_repository import BaseRepository
    from app.services import Services


class MegaCog(commands.Cog):
    """Базовый ког: держит бота и даёт типизированный доступ к сервисам и конфигу.

    Пример:
        class FunCog(MegaCog, name="Fun"):
            @property
            def fun_service(self) -> FunService:
                return self.services.fun_service
    """

    bot: MegaBot

    def __init__(self, bot: MegaBot) -> None:
        self.bot = bot

    @property
    def services(self) -> Services:
        services = self.bot.services
        if services is None:
            raise RuntimeError("Сервисы недоступны до завершения setup_hook")
        return services

    @property
    def config(self):
        return self.bot.config


RepoT = TypeVar("RepoT", bound="BaseRepository")


class BaseService(Generic[RepoT]):
    """Базовый сервис: единый доступ к своему репозиторию."""

    def __init__(self, repo: RepoT) -> None:
        self._repo = repo

    @property
    def repo(self) -> RepoT:
        return self._repo


__all__ = ["MegaCog", "BaseService"]
