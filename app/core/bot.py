"""Класс бота: инициализация БД, сервисов, когов и обработка ошибок команд."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from app.config import Config
from app.core import embeds
from app.core.presence import bot_activity

if TYPE_CHECKING:
    from app.core.root import Root
    from app.core.webpanel import WebPanel
    from app.db.backup_manager import DatabaseBackupManager
    from app.db.database import Database
    from app.services import Services

logger = logging.getLogger("bot")

_SUPPORT_HINT = "Если ошибка повторяется — посмотрите логи или обратитесь к поддержке."


class ClanBot(commands.Bot):
    config: Config
    db: Database | None
    services: Services | None
    root: Root | None
    webpanel: WebPanel | None
    db_backups: DatabaseBackupManager | None

    def __init__(self, config: Config) -> None:
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=config.prefix,
            intents=intents,
            help_command=None,
            activity=bot_activity(config.status_activity),
        )
        self.config = config
        self.db = None
        self.services = None
        self.root = None
        self.webpanel = None
        self.db_backups = None
        self._guild_commands_synced = False
        self.start_time = datetime.now(UTC)
        self.tree.on_error = self.on_app_command_error

    @property
    def uptime(self) -> timedelta:
        return datetime.now(UTC) - self.start_time

    async def setup_hook(self) -> None:
        from app.core.composition import assemble
        from app.core.loader import load_cogs, register_persistent_views
        from app.db.database import Database

        self.db = Database(self.config.database_url or self.config.db_path)
        await self.db.connect()
        from app.db.backup_manager import DatabaseBackupManager

        self.db_backups = DatabaseBackupManager(
            self.db,
            self.config.db_backup_dir,
            self.config.db_backup_interval_hours,
            self.config.db_backup_retention,
        )
        # Весь граф обязан ссылаться на текущий Discord-клиент. Без явной
        # передачи ``self`` composition root создаст второй ClanBot, и сервисы
        # (логи, музыка, тикеты) окажутся привязаны не к активному соединению.
        root = assemble(config=self.config, db=self.db, bot=self)
        self.root = root
        self.services = root.services
        loaded = await load_cogs(self)
        await register_persistent_views(self)
        if self.config.panel_port is not None:
            from app.core.webpanel import WebPanel

            self.webpanel = WebPanel(self)
            await self.webpanel.start()
        await self._sync_commands()
        # Запускаем первый backup только после bootstrap: SQLite backup API
        # использует очередь того же aiosqlite-соединения и не должен
        # конкурировать с регистрацией persistent views на старте.
        if self.db_backups is not None:
            self.db_backups.start()
        logger.info("Хук установки завершён: когов %d, views зарегистрированы", len(loaded))

    async def _sync_commands(self) -> None:
        if self.user is None or self.application_id is None:
            logger.debug("Синк команд: application_id ещё не известен — пропуск")
            return
        guild_id = self.config.guild_id
        sync_guild = discord.Object(id=guild_id) if guild_id is not None else None
        try:
            if sync_guild is not None:
                self.tree.copy_global_to(guild=sync_guild)
            synced = await self.tree.sync(guild=sync_guild)
        except (discord.HTTPException, discord.MissingApplicationID, discord.ConnectionClosed) as exc:
            logger.warning("Не удалось синхронизировать команды: %s", exc)
            return
        scope = f"гильдия {guild_id}" if guild_id is not None else "глобально"
        logger.info("Синхронизировано команд (%s): %d", scope, len(synced or []))

    async def on_ready(self) -> None:
        if self.config.guild_id is None and not self._guild_commands_synced:
            await self._sync_commands()
            self._guild_commands_synced = True

    async def close(self) -> None:
        webpanel = self.webpanel
        if webpanel is not None:
            try:
                await webpanel.stop()
            except Exception:
                logger.exception("Ошибка при остановке вебпанели")
            self.webpanel = None
        db = self.db
        backups = self.db_backups
        if backups is not None:
            try:
                await backups.stop()
            except Exception:
                logger.exception("Ошибка при остановке планировщика SQLite backup")
            self.db_backups = None
        try:
            await super().close()
        finally:
            if db is not None:
                try:
                    await db.close()
                except Exception:
                    logger.exception("Ошибка при закрытии БД")
                self.db = None

    async def on_app_command_error(self, interaction: discord.Interaction, error: Exception) -> None:
        original = getattr(error, "original", error)
        command = interaction.command.qualified_name if interaction.command else "?"
        location = self._interaction_location(interaction)

        known = (
            _BotMissingPermissions,
            app_commands.MissingPermissions,
            commands.NotOwner,
            app_commands.CommandOnCooldown,
            app_commands.TransformerError,
            app_commands.CheckFailure,
            commands.CommandError,
            discord.Forbidden,
            discord.HTTPException,
            discord.NotFound,
        )
        if isinstance(original, known):
            logger.warning("Ошибка команды /%s%s: %s", command, location, original)
        else:
            self._log_error("Ошибка команды /%s%s", error, command, location)
            await self._notify_error_feed(interaction.guild, f"Команда /{command}: {original}")

        if isinstance(original, _BotMissingPermissions):
            embed = embeds.error(
                "Боту не хватает прав",
                "Для этой команды боту нужны права: " + ", ".join(f"`{name}`" for name in original.missing) + ".",
            )
        elif isinstance(original, app_commands.MissingPermissions):
            names = ", ".join(f"`{name}`" for name in original.missing_permissions)
            embed = embeds.error("Недостаточно прав", f"Вам нужны права: {names}.")
        elif isinstance(original, commands.NotOwner):
            embed = embeds.error("Только для владельца", "Эта команда доступна владельцу бота.")
        elif isinstance(original, app_commands.CommandOnCooldown):
            embed = embeds.warning("Подождите", f"Команда на перезарядке: {original.retry_after:.1f} сек.")
        elif isinstance(original, discord.Forbidden):
            embed = embeds.error("Боту не хватает прав", "Проверьте права бота и иерархию ролей.")
        elif isinstance(original, discord.HTTPException):
            embed = embeds.error("Ошибка Discord API", str(original))
        elif isinstance(original, discord.NotFound):
            embed = embeds.error("Не найдено", "Объект (сообщение/пользователь/канал) больше не существует.")
        elif isinstance(original, app_commands.TransformerError):
            embed = embeds.error("Неверный аргумент", "Некоторые параметры не распознаны. Проверьте ввод.")
        elif isinstance(original, app_commands.CommandInvokeError):
            embed = embeds.error("Ошибка выполнения", _SUPPORT_HINT)
        elif isinstance(original, (app_commands.CheckFailure, commands.CommandError)):
            embed = embeds.error("Команда недоступна", str(original) or _SUPPORT_HINT)
        else:
            embed = embeds.error("Ошибка команды", _SUPPORT_HINT)

        await self._reply_error(interaction, embed)

    @staticmethod
    async def _reply_error(interaction: discord.Interaction, embed: discord.Embed) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            logger.debug("Не удалось отправить сообщение об ошибке", exc_info=True)

    # --- логирование всех ошибок ---

    @staticmethod
    def _log_error(message: str, error: Exception, *args: Any) -> None:
        """Пишет ошибку с полным трейсбеком в консоль и веб-ленту /logs."""
        if args:
            try:
                message = message % args
            except (TypeError, ValueError):
                pass
        logger.error("%s: %s", message, str(error), exc_info=(type(error), error, error.__traceback__))

    @staticmethod
    def _interaction_location(interaction: discord.Interaction) -> str:
        parts: list[str] = []
        if interaction.guild is not None:
            parts.append(f" [сервер: {interaction.guild.name}]")
        channel = interaction.channel
        if isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            parts.append(f" [канал: #{channel.name}]")
        return "".join(parts)

    @staticmethod
    def _guild_from_error_args(args: tuple[Any, ...]) -> discord.Guild | None:
        for arg in args:
            if isinstance(arg, discord.Guild):
                return arg
            if isinstance(arg, discord.Interaction) and arg.guild is not None:
                return arg.guild
            if isinstance(arg, discord.Message) and arg.guild is not None:
                return arg.guild
            if isinstance(arg, discord.Member):
                return arg.guild
            if isinstance(arg, (discord.TextChannel, discord.VoiceChannel, discord.Thread)) and arg.guild is not None:
                return arg.guild
        return None

    async def _notify_error_feed(self, guild: discord.Guild | None, text: str) -> None:
        """Дублирует ошибку в веб-ленту панели (/audit), если она доступна."""
        services = getattr(self, "services", None)
        if services is None or services.logging is None:
            return
        try:
            await services.logging.log_event(guild, "⛔ Ошибка", text[:1500])
        except Exception:
            logger.debug("Не удалось записать ошибку в веб-ленту", exc_info=True)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            logger.debug("Неизвестная префикс-команда: %s", ctx.message.content[:200])
            return
        command = ctx.command.qualified_name if ctx.command else (ctx.invoked_with or "?")
        if isinstance(
            error,
            (
                commands.MissingRequiredArgument,
                commands.TooManyArguments,
                commands.BadArgument,
                commands.ArgumentParsingError,
                commands.CheckFailure,
                commands.CommandOnCooldown,
                commands.NoPrivateMessage,
                commands.MissingPermissions,
                commands.BotMissingPermissions,
            ),
        ):
            logger.warning("Префикс-команда %s: %s", command, error)
            try:
                await ctx.reply(embed=embeds.error("Ошибка команды", str(error)), mention_author=False)
            except (discord.HTTPException, discord.Forbidden):
                pass
            return
        self._log_error("Ошибка префикс-команды %s", error, command)
        await self._notify_error_feed(ctx.guild, f"Префикс-команда {command}: {error}")

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        exc_type, exc_value, _traceback = sys.exc_info()
        guild = self._guild_from_error_args(args)
        logger.error("Необработанная ошибка в событии %s", event_method, exc_info=True)
        if exc_value is not None:
            await self._notify_error_feed(guild, f"Событие {event_method}: {type(exc_value).__name__}: {exc_value}")


class _BotMissingPermissions(app_commands.CheckFailure):
    """Собственная ошибка: у самого бота не хватает прав для команды."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"Боту не хватает прав: {', '.join(missing)}")
