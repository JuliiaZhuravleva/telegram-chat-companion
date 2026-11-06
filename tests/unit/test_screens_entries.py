"""The stable screen entries and the plumbing that hands them their data.

Three things are proven here, each at the call site rather than in a helper:

* the request container reaches ``show_hub`` from BOTH hub handlers through a
  real ``Dispatcher`` with ``setup_dishka(auto_inject=True)`` — the path that a
  direct handler call with mocks cannot see (`screens.deps`);
* the FSM context reaches an entry the same way, including from a handler
  that declares ``state`` and so loses it from ``**kwargs``;
* the stale catch-all clears only the panel's own prompts, hands the hub its
  line, and answers each kind of tapper in the right place;
* every stable entry refuses to render without ``data``.

What each entry DRAWS is pinned next to the area that owns it
(``test_rules_show_rules.py``, ``test_admin_chat_panel_entries.py``,
``test_hub_today.py``): this file must not go red because an area redesigned
its own screen.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from dishka import Provider, Scope, make_async_container
from dishka.integrations.aiogram import setup_dishka

from src.bot.handlers import admin
from src.bot.handlers import router as main_router
from src.bot.handlers.admin_chat_panel import show_chat, show_chats
from src.bot.handlers.rules import show_rules
from src.bot.screens import deps, stale
from src.bot.screens.deps import ContainerAliasMiddleware, container_of, install, state_of
from src.bot.states.admin import AdminStates
from src.database.repositories.admin import AdminRepository
from src.database.repositories.bot_config import BotConfigRepository

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


def test_state_of_reads_either_key_and_only_a_context():
    context = MagicMock(spec=FSMContext)
    assert state_of({deps.SCREEN_STATE: context}) is context
    assert state_of({"state": context}) is context
    assert state_of({}) is None
    assert state_of({"state": "awaiting_rule_config"}) is None


# ---------------------------------------------------------------------------
# A real Dispatcher: does the FSM context reach an entry?
# ---------------------------------------------------------------------------

BOT_ID = 42  # from the token "42:TEST"


async def _armed_after_stale(*, alias: bool) -> str | None:
    """A handler that declares ``state`` hands its ``**kwargs`` to `show_stale`
    while a panel prompt is armed; return the state left afterwards."""
    root = Router(name="probe-root")
    if alias:
        install(root)
    inner = Router(name="probe-live")

    @inner.callback_query(F.data.startswith("adm_probe:"))
    async def declares_state(
        callback: CallbackQuery,
        state: FSMContext,  # noqa: ARG001 — declaring it is what removes it from kwargs
        **kwargs: Any,
    ) -> None:
        assert "state" not in kwargs  # aiogram's half of the trap
        await stale.show_stale(callback, "ru", data=kwargs)

    root.include_router(inner)
    dp = Dispatcher()
    dp.include_router(root)
    key = StorageKey(bot_id=BOT_ID, chat_id=ADMIN_ID, user_id=ADMIN_ID)
    context = FSMContext(storage=dp.storage, key=key)
    await context.set_state(AdminStates.awaiting_rule_config)

    update = _button_update()
    assert update.callback_query is not None
    update = update.model_copy(
        update={"callback_query": update.callback_query.model_copy(update={"data": "adm_probe:ru"})}
    )
    bot = Bot(token="42:TEST")
    try:
        with patch.object(stale, "show_hub", new=AsyncMock()) as hub:
            await dp.feed_update(bot, update)
        assert hub.await_count == 1, "the probe handler did not run"
    finally:
        await bot.session.close()
    return await context.get_state()


@pytest.mark.asyncio
async def test_a_handler_that_declares_state_still_disarms_the_prompt():
    assert await _armed_after_stale(alias=True) is None


@pytest.mark.asyncio
async def test_control_without_the_alias_the_prompt_stays_armed():
    """The trap the alias closes: the prompt survives the stale tap and would
    take the admin's next message as a rule config."""
    assert await _armed_after_stale(alias=False) == AdminStates.awaiting_rule_config.state


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


@pytest.fixture
def hub() -> Any:
    """`show_hub` as the catch-all calls it. Patched: what the hub draws is the
    hub area's to change, and the stale contract is only the call."""
    with patch.object(stale, "show_hub", new=AsyncMock()) as spy:
        yield spy


@pytest.mark.asyncio
async def test_stale_shows_the_hub_with_its_line(hub):
    callback = _callback()
    kwargs = {"event_from_user": callback.from_user}
    await stale.handle_stale(callback, **kwargs)

    hub.assert_awaited_once_with(callback, "ru", notice_raw=stale.STALE_NOTICE("ru"), data=kwargs)
    callback.answer.assert_not_awaited()  # no popup: the line is the explanation


@pytest.mark.asyncio
async def test_stale_in_english(hub):
    await stale.handle_stale(_callback("ar_tog:en:1"))
    assert hub.await_args.args[1] == "en"
    assert hub.await_args.kwargs["notice_raw"] == stale.STALE_NOTICE("en")


@pytest.mark.asyncio
@pytest.mark.usefixtures("hub")
@pytest.mark.parametrize("key", ["state", deps.SCREEN_STATE])
async def test_stale_disarms_a_panel_prompt(key: str):
    state = _state("AdminStates:awaiting_rule_words")
    await stale.handle_stale(_callback(), **{key: state})
    state.clear.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("hub")
async def test_stale_leaves_a_foreign_state_alone():
    state = _state("SomeOtherFlow:waiting")
    await stale.handle_stale(_callback(), state=state)
    state.clear.assert_not_awaited()


def _tap(chat_type: str) -> CallbackQuery:
    chat_id = ADMIN_ID if chat_type == "private" else CHAT_ID
    return CallbackQuery(
        id="1",
        from_user=_user(),
        chat_instance="1",
        data="adm_nonexistent:ru",
        message=Message(
            message_id=1,
            date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            chat=Chat(id=chat_id, type=chat_type),
            text="old",
        ),
    )


async def _stale_handler_for(event: CallbackQuery, *, is_admin: bool) -> str | None:
    """The first stale handler whose REAL filters pass, as aiogram picks it.
    ``is_admin`` is the kwarg `IsAdmin` reads when the middleware has run."""
    for handler in stale.router.callback_query.handlers:
        passed, _ = await handler.check(event, is_admin=is_admin)
        if passed:
            return handler.callback.__name__
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_type", "is_admin", "expected"),
    [
        ("private", True, "handle_stale"),
        ("group", True, "handle_stale_outside_dm"),
        ("supergroup", True, "handle_stale_outside_dm"),
        ("private", False, "handle_stale_denied"),
        ("supergroup", False, "handle_stale_denied"),
    ],
)
async def test_each_tapper_gets_the_right_answer(chat_type: str, is_admin: bool, expected: str):
    assert await _stale_handler_for(_tap(chat_type), is_admin=is_admin) == expected


@pytest.mark.asyncio
async def test_a_bot_admin_outside_the_dm_is_told_where_the_panel_is():
    callback = _callback("adm_wl:en")
    await stale.handle_stale_outside_dm(callback)
    callback.answer.assert_awaited_once_with(
        "The panel opens in a private chat with the bot", show_alert=True
    )
    callback.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_denied_answers_with_an_alert():
    callback = _callback("adm_wl:en")
    await stale.handle_stale_denied(callback)
    callback.answer.assert_awaited_once_with("Access denied", show_alert=True)
    callback.message.edit_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# Stable entries: what they draw is pinned in each area's own tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        lambda: show_rules(_callback(), "ru", CHAT_ID, data={}),
        lambda: show_chat(_callback(), "ru", CHAT_ID, data={}),
        lambda: show_chats(_callback(), "ru", 0, data={}),
    ],
    ids=["show_rules", "show_chat", "show_chats"],
)
async def test_an_entry_without_data_refuses(entry):
    with pytest.raises(LookupError):
        await entry()
