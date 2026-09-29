"""RamReport: периодический отчёт по ОЗУ бота в канал. Порт Node ``ram_report.js``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import tasks

from app.core import embeds
from app.core.base import ClanCog

if TYPE_CHECKING:
    from app.core.bot import ClanBot

logger = logging.getLogger("bot.cogs")

_TRACEMALLOC_ROWS = 9
_TRACEMALLOC_COL = 60


def build_tracemalloc_report() -> str:
    """Отчёт трассировки аллокаций (tracemalloc) в стиле «Файл/строка | Память | Блоки»."""
    import tracemalloc

    if not tracemalloc.is_tracing():
        return ""
    snapshot = tracemalloc.take_snapshot()
    by_site: dict[tuple[str, int | None], list[int]] = {}
    for stat in snapshot.statistics("traceback"):
        frame = stat.traceback[0] if stat.traceback else None
        key = (frame.filename, frame.lineno) if frame is not None else ("<unknown>", None)
        bucket = by_site.setdefault(key, [0, 0])
        bucket[0] += stat.size
        bucket[1] += stat.count
    total = sum(bucket[0] for bucket in by_site.values())
    lines = ["Файл и строка" + " " * (_TRACEMALLOC_COL - 14) + "| " + "Память".rjust(13) + " | Кол-во блоков"]
    for (file_name, line), (size, count) in sorted(by_site.items(), key=lambda kv: kv[1][0], reverse=True)[:_TRACEMALLOC_ROWS]:
        location = f"{file_name}:{line}" if line is not None else file_name
        if len(location) > _TRACEMALLOC_COL:
            location = "..." + location[-_TRACEMALLOC_COL + 3 :]
        mb = size / (1024 * 1024)
        lines.append(f"{location:<{_TRACEMALLOC_COL}} | {mb:>9.2f} MB  | {count:>10}")
    body = "\n".join(lines)
    return f"Всего отслежено: **{total / (1024 * 1024):.2f} MB**\n```text\n{body}\n```"


def _fit_report(report: str) -> str:
    """Урезать body до 1024 символов (лимит значения поля embed)."""
    if len(report) <= 1024:
        return report
    prefix, _, body = report.partition("```text\n")
    if not _:
        return report[:1024]
    lines = body.splitlines()
    while len(lines) > 2 and len("\n".join(lines)) > 1000:
        lines.pop()
    return prefix + "```text\n" + "\n".join(lines) + "```"


class RamReportCog(ClanCog, name="RamReport"):
    def __init__(self, bot: ClanBot) -> None:
        super().__init__(bot)

    async def cog_load(self) -> None:
        conf = await self.module_config(self.bot.config.guild_id, "ram_report")
        if conf["channel_id"] is not None:
            self.ram_report_loop.change_interval(minutes=conf["interval_minutes"])
            self.ram_report_loop.start()
            logger.info(
                "Отчёт по ОЗУ: каждые %s мин в канал %s",
                conf["interval_minutes"],
                conf["channel_id"],
            )

    async def cog_unload(self) -> None:
        self.ram_report_loop.cancel()

    @tasks.loop(minutes=30)
    async def ram_report_loop(self) -> None:
        conf = await self.module_config(self.bot.config.guild_id, "ram_report")
        channel_id = conf["channel_id"]
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            logger.warning("Канал %s для отчёта по ОЗУ не найден", channel_id)
            return
        current = self._rss_mb()
        peak = self._peak_mb()
        embed = embeds.neutral("Монитор памяти", "Автоматическая диагностика процесса бота «BEDA».")
        embed.add_field(name="СЕЙЧАС", value=f"`{current:.1f} MB`", inline=True)
        embed.add_field(name="ПИК", value=f"`{peak:.1f} MB`", inline=True)
        embed.add_field(name="ИНТЕРВАЛ", value=f"`{conf['interval_minutes']} min`", inline=True)
        if conf["tracemalloc"]:
            report = _fit_report(build_tracemalloc_report())
            if report:
                embed.add_field(name="📊 Отчёт о памяти (tracemalloc)", value=report, inline=False)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            logger.exception("Ошибка отправки отчёта по ОЗУ")

    @ram_report_loop.before_loop
    async def before_ram_report_loop(self) -> None:
        await self.bot.wait_until_ready()

    def _rss_mb(self) -> float:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)

    def _peak_mb(self) -> float:
        import psutil

        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None)
        if peak is not None:
            return peak / (1024 * 1024)
        return info.rss / (1024 * 1024)
