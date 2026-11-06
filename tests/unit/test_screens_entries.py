"""The stable screen entries and the plumbing that hands them their data.

Three things are proven here, each at the call site rather than in a helper:

* the request container reaches ``show_hub`` from BOTH hub handlers through a
  real ``Dispatcher`` with ``setup_dishka(auto_inject=True)`` — the path that a
  direct handler call with mocks cannot see (`screens.deps`);
* the stale catch-all clears only the panel's own prompts and draws the hub;
* ``show_chat`` / ``show_chats`` / ``show_rules`` resolve their repositories
  from ``data`` and render exactly what today's button renders.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from dishka import Provider, Scope, make_async_container
from dishka.integrations.aiogram import setup_dishka

from src.bot.handlers import admin
from src.bot.handlers import router as main_router
from src.bot.handlers.admin_chat_panel import show_chat, show_chats
from src.bot.handlers.rules import show_rules
from src.bot.keyboards.rules import rules_list_keyboard
from src.bot.screens import deps, stale
from src.bot.screens.deps import ContainerAliasMiddleware, container_of, install
from src.bot.screens.hub import build_hub
from src.bot.screens.render import with_notice
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository
from src.database.repositories.chat_settings import ChatSettingsRepository
from src.database.repositories.rules import RulesRepository
from src.services.chat_config import ChatConfigService

ADMIN_ID = 555000111  # invented, from samples/fake.py's range
CHAT_ID = -1009999999999


# ---------------------------------------------------------------------------
# A real Dispatcher: does the container reach show_hub?
# ---------------------------------------------------------------------------


def _admin_repo() -> MagicMock:
    repo = MagicMock(spec=AdminRepository)
    repo.get_admin_language = AsyncMock(return_value="ru")
    return repo


def _dispatcher(admin_repo: MagicMock, *, alias: bool) -> Dispatcher:
    """The two hub handlers, registered as `handlers/__init__.py` registers them.

    A fresh router tree, because the real one can be attached to one
    dispatcher only; the handler FUNCTIONS are the real ones.
    """
    provider = Provider(scope=Scope.APP)
    provider.provide(lambda: admin_repo, provides=AdminRepository)
    provider.provide(lambda: MagicMock(spec=BotConfigRepository), provides=BotConfigRepository)

    root = Router(name="probe-root")
    if alias:
        install(root)
    inner = Router(name="probe-admin")
    inner.message.register(admin.handle_admin_command, Command("admin"))
    inner.callback_query.register(admin.handle_menu, F.data.startswith("adm_menu:"))
    root.include_router(inner)

    dp = Dispatcher()
    dp.include_router(root)
    setup_dishka(make_async_container(provider), router=dp, auto_inject=True)
    return dp


def _user() -> User:
    return User(id=ADMIN_ID, is_bot=False, first_name="Проба")


def _dm_message(text: str) -> Message:
    return Message(
        message_id=1,
        date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        chat=Chat(id=ADMIN_ID, type="private"),
        from_user=_user(),
        text=text,
    )


def _command_update() -> Update:
    return Update(update_id=1, message=_dm_message("/admin"))


def _button_update() -> Update:
    return Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="1",
            from_user=_user(),
            chat_instance="1",
            data="adm_menu:ru",
            message=_dm_message("old"),
        ),
    )


async def _feed(dp: Dispatcher, update: Update) -> list[Any]:
    """Feed one update; return what ``container_of(data)`` gave inside show_hub."""
    seen: list[Any] = []

    async def fake_show_hub(_target: Any, _lang: str, *, data: Any, **_kw: Any) -> None:
        try:
            seen.append(await container_of(data).get(AdminRepository))
        except LookupError as exc:
            seen.append(exc)

    bot = Bot(token="42:TEST")
    await dp.emit_startup(bot=bot)
    try:
        with patch.object(admin, "show_hub", side_effect=fake_show_hub) as spy:
            await dp.feed_update(bot, update, is_admin=True)
        assert spy.await_count == 1, "the handler did not reach show_hub"
    finally:
        await dp.emit_shutdown(bot=bot)
        await bot.session.close()
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [_command_update, _button_update], ids=["command", "button"])
async def test_both_hub_handlers_hand_show_hub_a_container(update):
    repo = _admin_repo()
    seen = await _feed(_dispatcher(repo, alias=True), update())
    assert seen == [repo]


@pytest.mark.asyncio
async def test_control_without_the_alias_the_command_has_no_container():
    """Without `install`, a handler that declares a ``FromDishka`` parameter
    loses ``dishka_container`` from its ``**kwargs`` — the measured trap."""
    seen = await _feed(_dispatcher(_admin_repo(), alias=False), _command_update())
    assert len(seen) == 1 and isinstance(seen[0], LookupError)


@pytest.mark.asyncio
async def test_control_without_the_alias_a_plain_handler_still_has_one():
    """The other half of the trap: ``handle_menu`` declares no ``FromDishka``,
    so it would have worked anyway — which is why testing only it hides the bug."""
    repo = _admin_repo()
    seen = await _feed(_dispatcher(repo, alias=False), _button_update())
    assert seen == [repo]


def test_the_main_router_carries_the_alias():
    for observer in (main_router.message, main_router.callback_query):
        assert any(isinstance(m, ContainerAliasMiddleware) for m in observer.middleware)


def test_no_container_is_an_error_not_an_empty_screen():
    with pytest.raises(LookupError, match="no Dishka container"):
        container_of({})
    marker = object()
    assert container_of({deps.DISHKA_KEY: marker}) is marker
    assert container_of({deps.SCREEN_CONTAINER: marker, deps.DISHKA_KEY: object()}) is marker


# ---------------------------------------------------------------------------
# Stale catch-all
# ---------------------------------------------------------------------------


def _callback(data: str = "adm_wl:ru") -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.from_user = _user()
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    return callback


def _state(current: str | None) -> MagicMock:
    state = MagicMock(spec=FSMContext)
    state.get_state = AsyncMock(return_value=current)
    state.clear = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_stale_shows_the_hub_with_its_line():
    callback = _callback()
    await stale.handle_stale(callback)

    callback.message.edit_text.assert_awaited_once()
    text = callback.message.edit_text.await_args.args[0]
    assert text == with_notice(build_hub("ru").text, notice_raw=stale.STALE_NOTICE("ru"))
    callback.answer.assert_awaited_once_with()  # no popup: the line is the explanation


@pytest.mark.asyncio
async def test_stale_in_english():
    callback = _callback("ar_tog:en:1")
    await stale.handle_stale(callback)
    assert callback.message.edit_text.await_args.args[0].startswith(stale.STALE_NOTICE("en"))


@pytest.mark.asyncio
async def test_stale_disarms_a_panel_prompt():
    state = _state("AdminStates:awaiting_rule_words")
    await stale.handle_stale(_callback(), state=state)
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_leaves_a_foreign_state_alone():
    state = _state("SomeOtherFlow:waiting")
    await stale.handle_stale(_callback(), state=state)
    state.clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_denied_answers_with_an_alert():
    callback = _callback("adm_wl:en")
    await stale.handle_stale_denied(callback)
    callback.answer.assert_awaited_once_with("Access denied", show_alert=True)
    callback.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Stable entries: show_chat, show_chats, show_rules
# ---------------------------------------------------------------------------


def _data(**repos: Any) -> dict[str, Any]:
    container = MagicMock()
    container.get = AsyncMock(side_effect=lambda dependency: repos[dependency.__name__])
    return {deps.SCREEN_CONTAINER: container}


@pytest.mark.asyncio
async def test_show_rules_renders_the_list_with_a_notice():
    rules_repo = MagicMock(spec=RulesRepository)
    rules_repo.get_rules_page = AsyncMock(return_value=([], 0))
    callback = _callback()

    await show_rules(
        callback, "ru", CHAT_ID, notice="Правило удалено", data=_data(RulesRepository=rules_repo)
    )

    rules_repo.get_rules_page.assert_awaited_once()
    assert rules_repo.get_rules_page.await_args.args[:2] == (CHAT_ID, 0)
    args, kwargs = callback.message.edit_text.await_args
    assert args[0].startswith("<i>✓ Правило удалено</i>\n\n")
    assert kwargs["reply_markup"] == rules_list_keyboard("ru", [], 0, 1, CHAT_ID)


@pytest.mark.asyncio
async def test_show_chats_renders_the_picker():
    admin_repo = MagicMock(spec=AdminRepository)
    admin_repo.get_enabled_chats_page_by_activity = AsyncMock(return_value=([], 0))
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await show_chats(message, "en", 3, data=_data(AdminRepository=admin_repo))

    # page 3 of an empty list clamps to the first page, as the button does
    assert admin_repo.get_enabled_chats_page_by_activity.await_args.args[0] == 0
    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_show_chat_resolves_its_three_dependencies():
    resolved = {
        "ChatSettingsRepository": MagicMock(spec=ChatSettingsRepository),
        "BotConfigRepository": MagicMock(spec=BotConfigRepository),
        "ChatConfigService": MagicMock(spec=ChatConfigService),
    }
    callback = _callback()
    with patch(
        "src.bot.handlers.admin_chat_panel.render_chat_panel",
        new=AsyncMock(return_value=("<b>Чат</b>", None)),
    ) as render:
        await show_chat(callback, "ru", CHAT_ID, data=_data(**resolved))

    render.assert_awaited_once_with(
        resolved["ChatSettingsRepository"],
        resolved["BotConfigRepository"],
        resolved["ChatConfigService"],
        "ru",
        CHAT_ID,
    )
    assert callback.message.edit_text.await_args.args[0] == "<b>Чат</b>"


@pytest.mark.asyncio
async def test_an_entry_without_data_refuses():
    with pytest.raises(LookupError):
        await show_rules(_callback(), "ru", CHAT_ID, data={})
