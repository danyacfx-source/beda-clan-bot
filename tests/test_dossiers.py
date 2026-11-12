"""Тесты личных дел: валидация анкеты, ассеты, репозиторий и сервис."""
from __future__ import annotations

import json
import os

import pytest

from app.cogs.administration.dossiers import TabButton, card_view, dossier_dynamic_items
from app.db.database import Database
from app.db.dossiers_repository import DossiersRepository
from app.services.dossier_service import (
    GEAR,
    SPECIALIZATIONS,
    DossierPermissionError,
    DossierService,
    DossierValidationError,
    available_gear,
    dossier_card,
    gear_embed,
    profile_embed,
    review_card,
    specialization_asset,
    validate_dossier,
    validate_gear,
)
from app.utils.text import clip, normalize_space, safe_component

GUILD = 1
CHANNEL = 10
OWNER = 100
ROLES = {"BEDA": 1, **{name: index + 2 for index, name in enumerate(SPECIALIZATIONS)}}


def _valid() -> dict[str, str]:
    return {
        "nickname": "Бэд",
        "name": "Иван",
        "city": "Москва",
        "steam_id": "76561198000000000",
        "primary": "Штурмовик",
        "secondary": "Медик",
    }


@pytest.fixture
async def db(tmp_path):
    database = Database(os.path.join(tmp_path, "test.db"))
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def repo(db):
    return DossiersRepository(db)


@pytest.fixture
def service(repo):
    return DossierService(repo)


@pytest.fixture
async def open_ticket(repo):
    """Открытый тикет заявителя: досье имеет силу только внутри него."""
    await repo.save_settings(GUILD, 500, json.dumps(ROLES))
    await repo.db.execute(
        "INSERT INTO tickets (guild_id, channel_id, creator_id, status, created_at) VALUES (?, ?, ?, 'open', ?)",
        (GUILD, CHANNEL, OWNER, "2026-09-29T12:00:00+00:00"),
    )
    return CHANNEL


# --- утилиты ввода ---

def test_normalize_space_убирает_невидимое():
    assert normalize_space("  Иван\u200b   Петров \n") == "Иван Петров"
    assert normalize_space(None) == ""


def test_clip_считает_эмодзи_за_две_единицы():
    """Суррогатная пара — две UTF-16 единицы, поэтому 3 эмодзи в лимит 2 не влезают."""
    assert clip("😀😀😀", 2) == "😀"
    assert clip("😀😀😀", 5) == "😀😀"
    assert clip("abc", 10) == "abc"


def test_safe_component_режет_путь():
    assert "/" not in safe_component("../../etc/passwd")
    assert safe_component("") == "file"
    assert safe_component("CON.png").startswith("_")


# --- валидация анкеты ---

def test_validate_dossier_принимает_корректную():
    assert validate_dossier(_valid())["city"] == "Москва"


@pytest.mark.parametrize("key,value", [("nickname", ""), ("name", " "), ("city", ""), ("steam_id", "")])
def test_validate_dossier_требует_поля(key, value):
    data = _valid()
    data[key] = value
    with pytest.raises(DossierValidationError):
        validate_dossier(data)


def test_validate_dossier_требует_разные_специализации():
    data = _valid()
    data["secondary"] = data["primary"]
    with pytest.raises(DossierValidationError, match="различаться"):
        validate_dossier(data)


def test_validate_dossier_отвергает_неизвестную_специализацию():
    data = _valid()
    data["primary"] = "Маг"
    with pytest.raises(DossierValidationError, match="обе специализации"):
        validate_dossier(data)


def test_validate_dossier_может_пропустить_источник():
    data = _valid()
    data["city"] = ""
    data["steam_id"] = ""
    assert validate_dossier(data, require_source=False)["city"] == ""


def test_validate_dossier_обрезает_слишком_длинное():
    data = _valid()
    data["nickname"] = "Я" * 200
    with pytest.raises(DossierValidationError, match="Ник"):
        validate_dossier(data)


# --- ассеты ---

def test_specialization_asset_находит_картинку():
    path = specialization_asset("Штурмовик")
    assert path.is_file()
    assert path.suffix == ".png"


def test_specialization_asset_отвергает_чужое_имя():
    with pytest.raises(DossierValidationError, match="Неизвестная"):
        specialization_asset("../../secrets")


# --- карточки ---

def test_dossier_card_содержит_все_поля():
    embed, primary = dossier_card(_valid(), OWNER)
    assert primary == "Штурмовик"
    names = [field.name for field in embed.fields]
    assert names == [
        "Имя",
        "Играет с сезона",
        "Основная роль",
        "Дополнительная роль",
        "Steam",
        "Дата вступления",
        "Город",
    ]
    assert embed.image.url == "attachment://specialization.png"


def test_review_card_помечает_пустые_поля():
    embed = review_card({**_valid(), "city": ""}, OWNER)
    city = next(field for field in embed.fields if field.name == "Город")
    assert city.value == "Не заполнено"


# --- репозиторий и сервис ---

async def test_setup_сохраняет_роли(repo, service):
    assert await service.settings(GUILD) is None
    await repo.save_settings(GUILD, 500, json.dumps(ROLES))
    assert await service.settings(GUILD) == ROLES


async def test_approve_требует_настройки(service, open_ticket):
    """Фикстура уже настроила форум, поэтому проверяем обратный порядок."""
    await service._repo.db.execute("DELETE FROM dossier_settings WHERE guild_id = ?", (GUILD,))
    with pytest.raises(DossierValidationError, match="setup_dossiers"):
        await service.approve(GUILD, OWNER, CHANNEL)


async def test_approve_открывает_анкету(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    assert await service.form_block_reason(GUILD, OWNER, CHANNEL) == ""


async def test_анкета_недоступна_без_допуска(service, open_ticket):
    reason = await service.form_block_reason(GUILD, OWNER + 1, CHANNEL)
    assert "одобренному" in reason


async def test_анкета_недоступна_вне_своего_тикета(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    reason = await service.form_block_reason(GUILD, OWNER, CHANNEL + 1)
    assert "одобренному" in reason


async def test_допуск_отваливается_после_закрытия_тикета(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    await service._repo.db.execute("UPDATE tickets SET status = 'closed' WHERE channel_id = ?", (CHANNEL,))
    assert "одобренному" in await service.form_block_reason(GUILD, OWNER, CHANNEL)


async def test_submit_draft_сохраняет_и_поднимает_ревизии(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))
    first = await service._repo.draft(GUILD, OWNER)
    assert first is not None and int(first["revision"]) == 1

    await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))
    second = await service._repo.draft(GUILD, OWNER)
    assert int(second["revision"]) == 2


async def test_submit_draft_требует_допуска(service, open_ticket):
    with pytest.raises(DossierValidationError, match="одобренному"):
        await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))


async def test_submit_draft_не_перезаписывает_опубликованное(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))
    await service._repo.reserve(GUILD, OWNER, json.dumps(_valid()))
    with pytest.raises(DossierValidationError, match="уже создано"):
        await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))


async def test_prepare_review_возвращает_данные(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))
    owner, data, revision, missing = await service.prepare_review(GUILD, CHANNEL, None, None)
    assert (owner, revision, missing) == (OWNER, 1, [])
    assert data["primary"] == "Штурмовик"


async def test_prepare_review_требует_открытый_тикет(service, open_ticket):
    with pytest.raises(DossierValidationError, match="нет заполненной анкеты"):
        await service.prepare_review(GUILD, CHANNEL, None, None)


async def test_prepare_review_уточняет_пустые_поля(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    data = validate_dossier(_valid(), require_source=False)
    data["city"] = ""
    data["steam_id"] = ""
    await service.submit_draft(GUILD, OWNER, CHANNEL, {**data, "primary": "Штурмовик", "secondary": "Медик"})

    owner, _, _, missing = await service.prepare_review(GUILD, CHANNEL, None, None)
    assert owner == OWNER and missing == ["city", "steam_id"]

    _, fixed, revision, missing = await service.prepare_review(GUILD, CHANNEL, "Москва", "76561198000000000")
    assert missing == [] and fixed["city"] == "Москва" and revision == 2


async def test_publish_требует_допуска(service, open_ticket):
    with pytest.raises(DossierValidationError, match="не одобрено"):
        await service.publish(_Guild(), OWNER, CHANNEL, 1)


async def test_publish_отвергает_устаревшую_ревизию(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))
    with pytest.raises(DossierValidationError, match="Анкета изменилась"):
        await service.publish(_Guild(), OWNER, CHANNEL, 99)


async def test_publish_не_дублирует_готовое_дело(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))
    await service._repo.reserve(GUILD, OWNER, json.dumps(_valid()))
    await service._repo.set_thread(GUILD, OWNER, 777)
    await service._repo.set_status(GUILD, OWNER, "ready")
    with pytest.raises(DossierValidationError, match="уже существует"):
        await service.publish(_Guild(), OWNER, CHANNEL, 1)


async def test_publish_блокируется_на_незавершённой_публикации(service, open_ticket):
    await service.approve(GUILD, OWNER, CHANNEL)
    await service.submit_draft(GUILD, OWNER, CHANNEL, validate_dossier(_valid()))
    await service._repo.reserve(GUILD, OWNER, json.dumps(_valid()))
    with pytest.raises(DossierValidationError, match="не завершена"):
        await service.publish(_Guild(), OWNER, CHANNEL, 1)


def test_resolve_roles_требует_управления_ролями():
    with pytest.raises(DossierPermissionError):
        DossierService(_Repo()).resolve_roles(_Guild(), ROLES)


def test_form_throttle_срабатывает():
    service = DossierService(_Repo())
    for _ in range(5):
        assert service._throttled("k", limit=5, window=300.0) == 0.0
    assert service._throttled("k", limit=5, window=300.0) > 0.0


class _Repo:
    pass


class _Guild:
    """Заглушка гильдии: проверки ролей и публикации не доходят до Discord."""

    id = GUILD
    filesize_limit = 25 * 1024 * 1024

    def get_role(self, _role_id: int) -> None:
        return None

    @property
    def me(self):
        return _Me()


class _Me:
    guild_permissions = type("P", (), {"manage_roles": False})()
    top_role = type("R", (), {})()


# --- роли, снаряжение, интерактивная карточка ---

async def _ready(repo, data=None):
    await repo.reserve(GUILD, OWNER, json.dumps(data or _valid(), ensure_ascii=False))
    await repo.set_thread(GUILD, OWNER, 777)
    await repo.set_status(GUILD, OWNER, "ready")


def test_специализации_и_каталог_снаряжения():
    assert SPECIALIZATIONS == ("Штурмовик", "Медик", "Поддержка", "Пилот", "Водитель", "Снайпер")
    assert set(GEAR) == set(SPECIALIZATIONS)
    assert GEAR["Поддержка"][0] == "Мина" and len(GEAR["Поддержка"]) == 6


def test_available_gear_основная_затем_дополнительная():
    assert available_gear(_valid()) == [*GEAR["Штурмовик"], *GEAR["Медик"]]
    assert available_gear({"primary": "Медик", "secondary": "Медик"}) == list(GEAR["Медик"])


def test_validate_gear_порядок_каталога_и_чужое():
    assert validate_gear(_valid(), ["Дефибриллятор", "M4"]) == ["M4", "Дефибриллятор"]
    with pytest.raises(DossierValidationError):
        validate_gear(_valid(), ["Apache"])
    with pytest.raises(DossierValidationError):
        validate_gear(_valid(), ["Несуществующее"])


def test_алиасы_старых_ролей():
    data = {**_valid(), "primary": "Механик", "secondary": "Строитель"}
    cleaned = validate_dossier(data)
    assert (cleaned["primary"], cleaned["secondary"]) == ("Водитель", "Поддержка")


async def test_настройки_со_старыми_ключами(repo, service):
    legacy = {"BEDA": 1, "Штурмовик": 2, "Медик": 3, "Механик": 4, "Строитель": 5, "Пилот": 6, "Снайпер": 7}
    await repo.save_settings(GUILD, 500, json.dumps(legacy, ensure_ascii=False))
    mapping = await service.settings(GUILD)
    assert mapping["Водитель"] == 4 and mapping["Поддержка"] == 5
    assert "Механик" not in mapping and set(SPECIALIZATIONS) <= set(mapping)


async def test_card_требует_готового_дела(service):
    with pytest.raises(DossierValidationError):
        await service.card(GUILD, OWNER)


async def test_update_profile_роли_без_регистра_и_очистка_снаряжения(repo, service):
    await _ready(repo, {**_valid(), "gear": ["FAL", "Дефибриллятор"]})
    data = await service.update_profile(
        GUILD, OWNER, 5, nickname=" Новый ", season="3", primary=" медик ", secondary="МЕХАНИК"
    )
    assert (data["primary"], data["secondary"]) == ("Медик", "Водитель")
    assert data["gear"] == ["Дефибриллятор"]
    assert data["updated_by"] == 5 and data["updated_at"]
    assert (await service.card(GUILD, OWNER))["nickname"] == "Новый"


async def test_update_profile_ошибки(repo, service):
    await _ready(repo)
    with pytest.raises(DossierValidationError, match="Штурмовик"):
        await service.update_profile(GUILD, OWNER, 5, nickname="a", season="", primary="Маг", secondary="Медик")
    with pytest.raises(DossierValidationError, match="различаться"):
        await service.update_profile(GUILD, OWNER, 5, nickname="a", season="", primary="Медик", secondary="медик")


async def test_update_identity_дата_и_steam(repo, service):
    await _ready(repo)
    data = await service.update_identity(
        GUILD, OWNER, 5, name="Пётр", steam_url="https://steamcommunity.com/id/x", joined_at="05.03.2025"
    )
    assert data["joined_at"] == "2025-03-05" and data["steam_url"].endswith("/id/x")
    data = await service.update_identity(GUILD, OWNER, 5, name="Пётр", steam_url="", joined_at="2024-01-02")
    assert data["joined_at"] == "2024-01-02" and data["steam_url"] == ""
    with pytest.raises(DossierValidationError, match="Steam"):
        await service.update_identity(GUILD, OWNER, 5, name="П", steam_url="http://evil.example", joined_at="")
    with pytest.raises(DossierValidationError, match="Дата"):
        await service.update_identity(GUILD, OWNER, 5, name="П", steam_url="", joined_at="31.02.2025")


async def test_update_gear_сохраняется(repo, service):
    await _ready(repo)
    await service.update_gear(GUILD, OWNER, 5, ["M4", "FAL"])
    assert (await service.card(GUILD, OWNER))["gear"] == ["FAL", "M4"]
    with pytest.raises(DossierValidationError):
        await service.update_gear(GUILD, OWNER, 5, ["Apache"])


async def test_dossier_by_thread(repo):
    await _ready(repo)
    row = await repo.dossier_by_thread(GUILD, 777)
    assert row is not None and int(row["owner_id"]) == OWNER
    assert await repo.dossier_by_thread(GUILD, 1) is None


def test_эмбеды_для_старых_данных():
    legacy = {**_valid(), "primary": "Механик", "secondary": "Медик"}
    embed = profile_embed(legacy, OWNER, None)
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Основная роль"] == "Водитель"
    assert fields["Играет с сезона"] == "Не заполнено" and fields["Steam"] == "76561198000000000"
    assert embed.title == "Бэд" and embed.description == f"<@{OWNER}>"
    assert embed.footer.text is None
    gear = gear_embed(legacy, OWNER)
    assert [field.name for field in gear.fields] == ["Водитель", "Медик"]
    assert "⬜ Humvee" in gear.fields[0].value


def test_эмбеды_с_новыми_данными():
    data = {**_valid(), "steam_url": "https://steamcommunity.com/id/x", "joined_at": "2025-03-05",
            "gear": ["M4"], "updated_at": "2026-01-01T00:00:00+00:00"}
    embed = profile_embed(data, OWNER, "Админ")
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Дата вступления"] == "05.03.2025" and "steamcommunity" in fields["Steam"]
    assert embed.footer.text == "Обновил Админ · BEDA" and embed.timestamp is not None
    assert "✅ M4" in gear_embed(data, OWNER).fields[0].value


def test_custom_id_кнопок_вкладок():
    assert dossier_dynamic_items() == (TabButton,)
    for tab in ("profile", "gear", "edit"):
        custom_id = TabButton(tab, OWNER).item.custom_id
        match = TabButton.__discord_ui_compiled_template__.fullmatch(custom_id)
        assert match is not None and match["tab"] == tab and int(match["owner"]) == OWNER


async def test_card_view_активная_вкладка():
    view = card_view(OWNER, "gear")
    styles = {child.item.label: child.item.style.name for child in view.children}
    assert styles == {"Профиль": "secondary", "Снаряжение": "primary", "Редактирование": "secondary"}
    assert view.timeout is None
