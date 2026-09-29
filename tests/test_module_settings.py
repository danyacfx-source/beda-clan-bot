"""Тесты точечных настроек модулей: реестр, сервис и хранилище."""
import os

import pytest

from app.config import Config
from app.core.module_settings import MODULE_SPECS, defaults_for, normalize
from app.db.database import Database
from app.db.module_settings_repository import ModuleSettingsRepository
from app.services.module_settings_service import ModuleSettingsService

GUILD_ID = 777


def _config(tmp: str) -> Config:
    return Config(
        token="x",
        prefix="!",
        db_path=os.path.join(tmp, "bot.db"),
        log_level="ERROR",
        status_activity="s",
        owner_id=None,
        temp_voice_trigger_ids=(111, 222),
        temp_voice_category_id=555,
    )


async def _service(tmp: str) -> tuple[ModuleSettingsService, Database]:
    db = Database(os.path.join(tmp, "bot.db"))
    await db.connect()
    service = ModuleSettingsService(ModuleSettingsRepository(db), _config(tmp))
    return service, db


def test_registry_has_modules() -> None:
    keys = {spec.key for spec in MODULE_SPECS}
    assert {"tempvoice", "events", "automod", "where_play"} <= keys


def test_defaults_read_env(tmp_path) -> None:
    conf = defaults_for("tempvoice", _config(str(tmp_path)))
    assert conf["trigger_ids"] == [111, 222]
    assert conf["category_id"] == 555


def test_defaults_tolerates_partial_config() -> None:
    class Partial:
        pass

    conf = defaults_for("tempvoice", Partial())
    assert conf["trigger_ids"] == []
    assert conf["category_id"] is None


def test_normalize_rejects_out_of_range() -> None:
    spec = next(s for s in MODULE_SPECS if s.key == "events")
    field = next(f for f in spec.fields if f.key == "reminder_lead_minutes")
    with pytest.raises(ValueError):
        normalize(field, 0)
    with pytest.raises(ValueError):
        normalize(field, 999)
    assert normalize(field, "30") == 30


@pytest.mark.asyncio
async def test_service_override_and_reset(tmp_path) -> None:
    service, db = await _service(str(tmp_path))
    try:
        base = await service.get(GUILD_ID, "tempvoice")
        assert base["trigger_ids"] == [111, 222]

        updated = await service.update(GUILD_ID, "tempvoice", {"trigger_ids": [9, 8]})
        assert updated["trigger_ids"] == [9, 8]
        assert (await service.get(GUILD_ID, "tempvoice"))["trigger_ids"] == [9, 8]

        await service.reset(GUILD_ID, "tempvoice")
        assert (await service.get(GUILD_ID, "tempvoice"))["trigger_ids"] == [111, 222]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_service_blank_clears_override(tmp_path) -> None:
    service, db = await _service(str(tmp_path))
    try:
        await service.update(GUILD_ID, "tempvoice", {"category_id": 42})
        assert (await service.get(GUILD_ID, "tempvoice"))["category_id"] == 42

        await service.update(GUILD_ID, "tempvoice", {"category_id": ""})
        assert (await service.get(GUILD_ID, "tempvoice"))["category_id"] == 555
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_service_rejects_unknown_field(tmp_path) -> None:
    service, db = await _service(str(tmp_path))
    try:
        with pytest.raises(ValueError):
            await service.update(GUILD_ID, "tempvoice", {"nope": 1})
    finally:
        await db.close()
