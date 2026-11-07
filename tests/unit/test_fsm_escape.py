"""Every FSM prompt must have a way out that does not depend on valid input (TD-049).

These tests drive the REAL `main_router.propagate_event`, not the handlers
directly, because the defect and the fix both live in routing. `/cancel` is
correct in isolation and useless at the wrong include position: measured with
the cancel router appended LAST, `/cancel` in `awaiting_rule_config` is
consumed by the rules handler and answered «Невалидный JSON» — the cancel
handler never runs. Registration is not firing (CLAUDE.md), so a test that
imports the handler and calls it proves nothing about the bug.

A real `aiogram.types.Message` is required rather than a MagicMock for the
same reason the sticker router-order tests need one: aiogram's `Command`
filter opens with `isinstance(message, Message)` and returns False otherwise,
so with a mock every command handler in the chain is skipped — which is
indistinguishable from the bug. `data["bot"]` must be present too, because
`Command.__call__(self, message, bot)` resolves `bot` from the handler data.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.state import State
from aiogram.types import Chat, Message, MessageOriginUser, PhotoSize, User

from src.bot.handlers import router as main_router
from src.bot.handlers.fsm_cancel import router as fsm_cancel_router
from src.bot.states.admin import AdminStates
from src.models.chat_config import ChatConfig

ADMIN_ID = 555
CHAT_ID = 555


class _FakeState:
    """An FSMContext stand-in that records what the handlers did to it.

    Both `get_state` and `clear` must be awaitable. A bare MagicMock makes
    `await state.clear()` raise TypeError *inside* the handler, and in a test
    asserting that a handler ran, that renders as a routing failure rather
    than as a fixture gap — the project's documented "the mock can't take the
    real call" trap.
    """

    def __init__(self, state: State | None = None) -> None:
        self._state: str | None = state.state if state is not None else None
        self._data: dict[str, object] = {}
        self.clear_calls = 0

    async def get_state(self) -> str | None:
        return self._state

    async def set_state(self, state: State | None = None) -> None:
        self._state = state.state if isinstance(state, State) else state

    async def get_data(self) -> dict[str, object]:
        return dict(self._data)

    async def update_data(self, **kwargs: object) -> dict[str, object]:
        self._data.update(kwargs)
        return dict(self._data)

    async def set_data(self, data: dict[str, object]) -> None:
        self._data = dict(data)

    async def clear(self) -> None:
        self.clear_calls += 1
        self._state = None
        self._data = {}


def _make_message(
    *,
    text: str | None = None,
    caption: str | None = None,
    forwarded: bool = False,
    is_admin: bool = True,
    language_code: str | None = None,
) -> tuple[Message, dict[str, object], AsyncMock]:
    bot = AsyncMock()
    chat = Chat(id=CHAT_ID, type="private")
    # aiogram models are frozen, so the locale has to be set at construction.
    user = User(id=ADMIN_ID, is_bot=False, first_name="Admin", language_code=language_code)
    date = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)

    photo = (
        [PhotoSize(file_id="p", file_unique_id="pu", width=1, height=1)]
        if caption is not None
        else None
    )
    msg = Message(
        message_id=1,
        date=date,
        chat=chat,
        from_user=user,
        text=text,
        caption=caption,
        photo=photo,
        forward_origin=(
            MessageOriginUser(
                type="user", date=date, sender_user=User(id=99, is_bot=False, first_name="F")
            )
            if forwarded
            else None
        ),
    ).as_(bot)

    chat_settings_repo = MagicMock()
    chat_settings_repo.get = AsyncMock(return_value=None)
    chat_settings_repo.set_field = AsyncMock()

    data: dict[str, object] = {
        "is_admin": is_admin,
        "bot": bot,
        "event_from_user": user,
        "bot_id": 424242,
        "message_thread_id": None,
        "rules_repo": AsyncMock(),
        "chat_settings_repo": chat_settings_repo,
        # `/cancel` resolves its language the way the rest of the admin surface
        # does, so the handler now takes both of these from DI.
        "bot_config_repo": AsyncMock(),
        "admin_repo": _admin_repo(),
        "message_repo": AsyncMock(),
        # The KB organizer handlers name people through it; nobody is stored.
        "organizer_names": AsyncMock(
            names=AsyncMock(return_value={}),
            remember=AsyncMock(return_value=True),
            remember_seen=AsyncMock(return_value=False),
        ),
        "knowledge_repo": AsyncMock(),
        "sticker_repo": MagicMock(),
        "sticker_service": AsyncMock(),
        # A real ChatConfig, not a mock: the generic `F.text` catch-all at the
        # end of the chain reads `random_response_chance` and compares it to a
        # float, so a MagicMock raises TypeError *inside* the last handler and
        # every fall-through assertion turns into a crash.
        "chat_config": ChatConfig(
            chat_id=CHAT_ID, enabled=True, trigger_words=(), random_response_chance=0.0
        ),
        "pipeline": AsyncMock(),
        "relevancy_gate": AsyncMock(),
        "spend_limit_svc": AsyncMock(),
        "abuse_checker": AsyncMock(),
        "chat_config_service": MagicMock(),
        "summary_service": AsyncMock(),
        "knowledge_service": AsyncMock(),
    }
    return msg, data, bot


def _admin_repo(lang: str = "ru") -> AsyncMock:
    repo = AsyncMock()
    repo.get_admin_language = AsyncMock(return_value=lang)
    return repo


def _sent_texts(bot: AsyncMock) -> list[str]:
    """What the user would actually have received, read off the bot itself."""
    return [
        call.args[0].text
        for call in bot.await_args_list
        if call.args and isinstance(getattr(call.args[0], "text", None), str)
    ]


async def _propagate(msg: Message, data: dict[str, object], state: _FakeState) -> object:
    data["state"] = state
    data["raw_state"] = await state.get_state()
    return await main_router.propagate_event("message", msg, **data)


class TestCancelIsReachableFromEveryState:
    async def test_cancel_escapes_the_rule_config_prompt(self) -> None:
        """The trap this whole item exists for.

        Before: the rules handler had no command filter, so `/cancel` — like
        every other command — was answered «Невалидный JSON» and the state
        survived. `MemoryStorage` has no TTL, so nothing else would ever have
        cleared it.
        """
        msg, data, bot = _make_message(text="/cancel")
        state = _FakeState(AdminStates.awaiting_rule_config)

        await _propagate(msg, data, state)

        assert state.clear_calls == 1, "the stuck state was not cleared"
        assert _sent_texts(bot) == ["Отменено."], (
            "another handler consumed /cancel before fsm_cancel saw it"
        )

    async def test_cancel_with_no_dialog_open_says_so(self) -> None:
        """Matching with no state is wanted: consuming an update and silently
        doing nothing is indistinguishable from a broken bot."""
        msg, data, bot = _make_message(text="/cancel")
        state = _FakeState(None)

        await _propagate(msg, data, state)

        assert _sent_texts(bot) == ["Нечего отменять."]

    async def test_ordinary_text_is_left_alone(self) -> None:
        """The escape must not become another catch-all."""
        msg, data, bot = _make_message(text="just talking")
        state = _FakeState(None)

        await _propagate(msg, data, state)

        assert state.clear_calls == 0
        assert "Отменено." not in _sent_texts(bot)

    def test_the_cancel_router_is_included_first(self) -> None:
        """Position IS the fix, so it is asserted as a fact and not implied.

        The behavioural tests above would also pass if the router were merely
        *ahead of rules*; this pins the property those tests rely on.
        """
        assert main_router.sub_routers.index(fsm_cancel_router) == 0


_RULE_PROMPTS = (
    AdminStates.awaiting_rule_config,
    AdminStates.awaiting_rule_words,
    AdminStates.awaiting_rule_text,
    AdminStates.awaiting_rule_react,
)
"""The rules area's four typed-input prompts: each one has the same filters."""

_RULE_DRAFT: dict[str, object] = {
    "rule_chat_id": -100123,
    "lang": "ru",
    "rule_type": "keyword_trigger",
    "rule_words": ["реклама"],
    "rule_match": "contains",
    "rule_name": "реклама",
    "rule_action": "warn_user",
    "rule_prompt_id": None,
}
"""A builder draft one answer short of a rule, so any prompt's handler can act on it.

Without one, a prompt's handler leaves silently, and a test that a command is
not swallowed would pass with the filters removed.
"""


async def _armed(prompt: State) -> _FakeState:
    state = _FakeState(prompt)
    await state.update_data(**_RULE_DRAFT)
    return state


def _with_rule_services(data: dict[str, object]) -> None:
    """Services a created rule's card reads, awaitable like the real ones."""
    service = MagicMock()
    service.get_config = AsyncMock(return_value=ChatConfig(chat_id=-100123))
    data["chat_config_service"] = service


async def _untouched(state: _FakeState, prompt: State, data: dict[str, object]) -> bool:
    """No rules handler acted: the prompt, its draft and the repository are as armed."""
    return (
        await state.get_state() == prompt.state
        and await state.get_data() == _RULE_DRAFT
        and not data["rules_repo"].create.await_count  # type: ignore[union-attr]
    )


class TestCommandsEscapeTheRulePrompts:
    @pytest.mark.parametrize("prompt", _RULE_PROMPTS, ids=lambda s: s.state)
    async def test_a_typed_command_is_not_swallowed(self, prompt: State) -> None:
        msg, data, bot = _make_message(text="/help")
        _with_rule_services(data)
        state = await _armed(prompt)

        await _propagate(msg, data, state)

        assert await _untouched(state, prompt, data)
        assert "Невалидный JSON. Попробуйте ещё раз." not in _sent_texts(bot)

    @pytest.mark.parametrize("prompt", _RULE_PROMPTS, ids=lambda s: s.state)
    async def test_a_command_in_a_photo_caption_is_not_swallowed(self, prompt: State) -> None:
        """aiogram reads a command from `text or caption`.

        So `/help` under a photo is a real command — and `~F.text.startswith("/")`
        returns True for it, because `.text` is None. Measured before the fix:
        the rules handler consumed it and answered «Невалидный JSON». This is
        the case the text-only guard used elsewhere in the project cannot see.
        """
        msg, data, bot = _make_message(caption="/help")
        _with_rule_services(data)
        state = await _armed(prompt)

        await _propagate(msg, data, state)

        assert await _untouched(state, prompt, data)

    @pytest.mark.parametrize(
        ("prompt", "text"),
        [
            (AdminStates.awaiting_rule_config, "not json at all"),
            (AdminStates.awaiting_rule_words, "реклама, купи"),
            (AdminStates.awaiting_rule_text, "Давайте без спама"),
            (AdminStates.awaiting_rule_react, "👍"),
        ],
        ids=lambda v: getattr(v, "state", None) or "text",
    )
    async def test_ordinary_text_still_reaches_the_prompt(self, prompt: State, text: str) -> None:
        """The paired positive: the filters must not disable the dialog itself."""
        msg, data, bot = _make_message(text=text)
        _with_rule_services(data)
        state = await _armed(prompt)

        await _propagate(msg, data, state)

        assert not await _untouched(state, prompt, data)

    async def test_invalid_json_says_so(self) -> None:
        msg, data, bot = _make_message(text="not json at all")
        state = await _armed(AdminStates.awaiting_rule_config)

        await _propagate(msg, data, state)

        assert "Невалидный JSON. Попробуйте ещё раз." in _sent_texts(bot)
        assert await state.get_state() == AdminStates.awaiting_rule_config.state


class TestRuleCreationRequiresAuthorityAtTheTime_Of_Writing:
    async def test_a_demoted_admin_cannot_complete_the_dialog(self) -> None:
        """S-11: the FSM key binds the state to the user who *was* an admin.

        `handle_rule_config_input` writes a rule for whatever chat_id the FSM
        data carries and, until TD-049, did so with no authority check of any
        kind — unlike both of its siblings. Valid JSON is used on purpose: any
        input the handler rejects for another reason would make this pass with
        the filter removed.
        """
        msg, data, bot = _make_message(text='{"action": "warn_user"}', is_admin=False)
        state = _FakeState(AdminStates.awaiting_rule_config)
        await state.update_data(rule_chat_id=-100123, rule_type="spam_detect", lang="ru")

        await _propagate(msg, data, state)

        data["rules_repo"].create.assert_not_awaited()  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        ("prompt", "text"),
        [
            (AdminStates.awaiting_rule_text, "Давайте без спама"),
            (AdminStates.awaiting_rule_react, "👍"),
        ],
        ids=lambda v: getattr(v, "state", None) or "text",
    )
    async def test_nor_the_builder_prompts(self, prompt: State, text: str) -> None:
        """The builder's last typed answer writes a rule the same way."""
        msg, data, bot = _make_message(text=text, is_admin=False)
        _with_rule_services(data)
        state = await _armed(prompt)

        await _propagate(msg, data, state)

        data["rules_repo"].create.assert_not_awaited()  # type: ignore[union-attr]


def _callbacks(source: str) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse(source)
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any("callback_query" in ast.unparse(d) for d in node.decorator_list)
    }


def _unguarded(source: str, setters: Mapping[str, str]) -> list[str]:
    """Callback handlers that neither leave a rule prompt nor are declared setters."""
    return [
        f"{name}() at rules.py:{node.lineno}"
        for name, node in _callbacks(source).items()
        if name not in setters and "_leave_config_prompt" not in ast.unparse(node)
    ]


class TestEveryRulesCallbackLeavesTheDialog:
    """The structural guard against the next omission.

    The first version of this fix wired `_leave_config_prompt` into four of the
    eight rules callbacks. Tapping 🔄 or 🗑 on an older, still-interactive
    rules-list message re-rendered that list — visually leaving the prompt —
    while the FSM state survived, so the next plain message either drew a
    stray «Невалидный JSON» or silently created a rule in the earlier chat.

    Asserting per-handler rather than by behaviour on purpose: a behavioural
    test only covers the handlers someone remembered to write one for, which is
    the same failure mode one level up. The handlers that SET a prompt cannot
    start by leaving one; they are named in `rules._STATE_SETTERS`, each with
    its reason, and the second test holds that list to what it claims.
    """

    def test_all_of_them_call_the_helper(self) -> None:
        from src.bot.handlers import rules

        missing = _unguarded(inspect.getsource(rules), rules._STATE_SETTERS)

        assert missing == [], (
            "these rules callbacks navigate away from a rule prompt without "
            "ending it, so a later message is misapplied to the earlier chat:\n  "
            + "\n  ".join(missing)
        )

    def test_the_guard_sees_a_handler_that_forgets(self) -> None:
        """Known-positive control: an empty result above must mean "none", not "blind"."""
        source = (
            '@router.callback_query(F.data.startswith("x:"))\n'
            "async def forgot(callback, state):\n"
            "    await show(callback)\n"
            '@router.callback_query(F.data.startswith("y:"))\n'
            "async def remembered(callback, state):\n"
            "    await _leave_config_prompt(state)\n"
            '@router.callback_query(F.data.startswith("z:"))\n'
            "async def sets(callback, state):\n"
            "    await state.set_state(None)\n"
        )
        assert _unguarded(source, {}) == ["forgot() at rules.py:2", "sets() at rules.py:8"]
        assert _unguarded(source, {"sets": "arms a prompt"}) == ["forgot() at rules.py:2"]

    def test_every_setter_is_excluded_for_a_reason(self) -> None:
        """Paired negative: the exclusions must be setters, not holes.

        Each one is a callback in the module, sets a state, and has a reason. A
        setter that left the prompt BEFORE arming its own would end the dialog
        the instant it opened, so the first `set_state` must come before any
        call to the helper.
        """
        from src.bot.handlers import rules

        callbacks = _callbacks(inspect.getsource(rules))
        problems = []
        for name, reason in rules._STATE_SETTERS.items():
            node = callbacks.get(name)
            if node is None:
                problems.append(f"{name}: not a callback handler in rules.py")
                continue
            if not reason.strip():
                problems.append(f"{name}: no reason given")
            source = ast.unparse(node)
            arms = source.find("set_state(AdminStates.")
            leaves = source.find("_leave_config_prompt(")
            if arms < 0:
                problems.append(f"{name}: arms no prompt, so it needs no exclusion")
            elif 0 <= leaves < arms:
                problems.append(f"{name}: leaves the prompt before arming one")
        assert problems == []


class TestCancelSpeaksTheAdminsLanguage:
    async def test_it_reads_the_configured_panel_language(self) -> None:
        """Not the client locale.

        `language_code` carries region subtags — `en-US`, `pt-br` are real
        values — which match neither key and fall through to Russian, making
        the English string unreachable. And an admin who set the panel to
        English on a Russian client would get one Russian string among English
        ones.
        """
        msg, data, bot = _make_message(text="/cancel")
        data["admin_repo"] = _admin_repo("en")
        state = _FakeState(AdminStates.awaiting_rule_config)

        await _propagate(msg, data, state)

        assert _sent_texts(bot) == ["Cancelled."]

    async def test_the_client_locale_does_not_override_the_panel(self) -> None:
        """The discriminating case, and it has to be chosen carefully.

        `language_code="en-US"` + panel Russian is NOT discriminating: the
        client-locale implementation looks up "en-US", misses both keys, and
        falls back to Russian — the same answer the correct implementation
        gives. (Measured: that version of this test passed against the very
        implementation it was meant to reject.) Panel English + a Russian
        client separates them: only reading the panel yields English.
        """
        msg, data, bot = _make_message(text="/cancel", language_code="ru")
        data["admin_repo"] = _admin_repo("en")
        state = _FakeState(AdminStates.awaiting_rule_config)

        await _propagate(msg, data, state)

        assert _sent_texts(bot) == ["Cancelled."]

    async def test_a_region_subtag_does_not_silently_fall_back_to_russian(self) -> None:
        """`en-US`, `pt-br` are real values Telegram sends. Under a client-locale
        lookup they match neither key, so the English string is unreachable for
        those clients — panel language or not."""
        msg, data, bot = _make_message(text="/cancel", language_code="en-US")
        data["admin_repo"] = _admin_repo("en")
        state = _FakeState(AdminStates.awaiting_rule_config)

        await _propagate(msg, data, state)

        assert _sent_texts(bot) == ["Cancelled."]


def _rules_callback(data: str) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.chat = MagicMock()
    callback.message.chat.type = "private"
    callback.message.edit_text = AsyncMock()
    return callback


def _rules_deps() -> tuple[AsyncMock, MagicMock, MagicMock]:
    """What a rules screen reads: the rules, the chat's merged config, its row."""
    rules_repo = AsyncMock()
    rules_repo.get_rules_page = AsyncMock(return_value=([], 0))
    service = MagicMock()
    service.get_config = AsyncMock(return_value=ChatConfig(chat_id=-100123))
    settings = MagicMock()
    settings.get = AsyncMock(return_value=None)
    return rules_repo, service, settings


class TestNavigatingAwayEndsTheDialog:
    @pytest.mark.parametrize("prompt", _RULE_PROMPTS, ids=lambda s: s.state)
    async def test_going_back_to_the_rule_list_drops_the_prompt(self, prompt: State) -> None:
        """Otherwise the state outlives the screen that created it.

        Concrete misfire: open the type prompt for chat A, tap «Назад», wander
        off, then paste any valid JSON dict into the DM an hour later — a rule
        is silently created in chat A with the type chosen back then. Neither
        /cancel nor the cancel button covers it, because the admin never
        realises a dialog is still open. The builder's prompts have the same
        shape: a stray message would become a rule's words, text or reaction.
        """
        from src.bot.handlers.rules import handle_rules_list

        state = await _armed(prompt)

        await handle_rules_list(
            _rules_callback("ar_list:ru:-100123:0"), state, *_rules_deps(), is_admin=True
        )

        assert state.clear_calls == 1
        assert await state.get_state() is None

    async def test_an_unrelated_dialog_is_not_ended(self) -> None:
        """`state.clear()` is indiscriminate; a rules screen has no business
        ending someone else's dialog, so only OUR state is dropped."""
        from src.bot.handlers.rules import handle_rules_list

        state = _FakeState(AdminStates.awaiting_kb_organizer)

        await handle_rules_list(
            _rules_callback("ar_list:ru:-100123:0"), state, *_rules_deps(), is_admin=True
        )

        assert state.clear_calls == 0
        assert await state.get_state() == AdminStates.awaiting_kb_organizer.state


class TestTheCancelButtonWorks:
    async def test_the_button_clears_the_state_and_renders_the_list(self) -> None:
        """The on-screen escape, paired with /cancel.

        Worth its own test rather than trusting the keyboard: the prompt used
        to be rendered with no `reply_markup` at all, so `edit_text` stripped
        the keyboard the admin arrived on and the screen offered nothing to
        press. This is an old button (``ar_cancel:``) that still answers.
        """
        from src.bot.handlers.rules import handle_rule_config_cancel

        state = _FakeState(AdminStates.awaiting_rule_config)
        await state.update_data(rule_chat_id=-100123, rule_type="spam_detect", lang="ru")
        callback = _rules_callback("ar_cancel:ru:-100123")

        await handle_rule_config_cancel(callback, state, *_rules_deps(), is_admin=True)

        assert state.clear_calls == 1
        callback.message.edit_text.assert_awaited_once()
        assert callback.message.edit_text.await_args.kwargs["reply_markup"] is not None

    async def test_it_does_not_end_an_unrelated_dialog(self) -> None:
        """These cancel buttons ride on standalone reply messages that nothing
        ever edits, so they stay tappable indefinitely. Tapping a stale one
        while parked in a DIFFERENT dialog must not wipe that dialog — the rule
        the sibling helper in this same file states out loud."""
        from src.bot.handlers.rules import handle_rule_config_cancel

        state = _FakeState(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")

        await handle_rule_config_cancel(
            _rules_callback("ar_cancel:ru:-100123"), state, *_rules_deps(), is_admin=True
        )

        assert state.clear_calls == 0
        assert await state.get_state() == AdminStates.awaiting_kb_organizer.state
        assert (await state.get_data())["kb_chat_id"] == -100123, (
            "the other dialog's data was wiped along with its state"
        )

    async def test_a_non_admin_cannot_drive_it(self) -> None:
        """It re-renders a chat's rule list, so it is not harmless navigation."""
        from src.bot.handlers.rules import handle_rule_config_cancel

        state = _FakeState(AdminStates.awaiting_rule_config)
        callback = _rules_callback("ar_cancel:ru:-100123")
        rules_repo, service, settings = _rules_deps()

        await handle_rule_config_cancel(
            callback, state, rules_repo, service, settings, is_admin=False
        )

        rules_repo.get_rules_page.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()


class TestForwardingStillWorksForOrganizers:
    async def test_a_forwarded_message_whose_text_is_a_command_still_adds(self) -> None:
        """The regression the obvious slash guard would have caused.

        `handle_kb_organizer_add_reply` serves two inputs — a forward, and a
        typed @username. A bare `~F.text.startswith("/")` also rejects a
        FORWARDED message that happens to read "/kb", which is a legitimate
        organizer add: it would be routed to the /kb command handler instead,
        the organizer would not be added, and the state would stay set.
        """
        from src.bot.handlers import admin_kb

        msg, data, bot = _make_message(text="/kb", forwarded=True)
        state = _FakeState(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")

        # Asserting on the OUTCOME (the organizer is actually written), not on
        # `state.clear()`. The clear happens on both the authorized and the
        # demoted path, so using it as the signal would make this test depend
        # on where the clear sits — and a control that moves the clear would
        # then redden this test too, for a reason that has nothing to do with
        # forwarding.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))
            await _propagate(msg, data, state)

        data["chat_settings_repo"].set_field.assert_awaited_once()  # type: ignore[union-attr]
        assert data["chat_settings_repo"].set_field.await_args.args[1] == "kb_organizer_ids"  # type: ignore[union-attr]


class TestCommandsEscapeTheOrganizerPrompt:
    """The other half of admin_kb's guard.

    `TestForwardingStillWorksForOrganizers` proves the guard did not BREAK the
    forward path. That says nothing about whether it still lets a typed command
    out — and a guard written as `F.forward_origin.is_not(None) | ...` fails
    open on exactly that side if the right-hand clause is dropped.
    """

    async def test_a_typed_command_is_not_swallowed(self) -> None:
        """`check_admin_direct` is patched TRUE on purpose.

        Left unpatched it resolves against an AsyncMock repo, comes back falsy,
        and the handler returns before replying — so the test passes with the
        slash guard deleted, proving nothing. Measured: that is exactly what
        happened, and the control caught it. With the admin check satisfied,
        swallowing `/help` produces a visible «Не нашёл такого участника».
        """
        from src.bot.handlers import admin_kb

        msg, data, bot = _make_message(text="/help")
        state = _FakeState(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))
            await _propagate(msg, data, state)

        assert "Не нашёл такого участника" not in " ".join(_sent_texts(bot)), (
            "the organizer prompt ate a typed command"
        )

    async def test_a_command_in_a_photo_caption_is_not_swallowed(self) -> None:
        """Same caption hole as the rules prompt: `.text` is None for these."""
        from src.bot.handlers import admin_kb

        msg, data, bot = _make_message(caption="/help")
        state = _FakeState(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))
            await _propagate(msg, data, state)

        assert "Не нашёл такого участника" not in " ".join(_sent_texts(bot)), (
            "the organizer prompt ate a command sent as a photo caption"
        )

    async def test_a_typed_username_still_reaches_the_handler(self) -> None:
        """The paired positive: the guard must not disable the typed path."""
        from src.bot.handlers import admin_kb

        msg, data, bot = _make_message(text="@someone")
        state = _FakeState(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")
        data["message_repo"].find_by_username = AsyncMock(return_value={"user_id": 77})  # type: ignore[union-attr]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))
            await _propagate(msg, data, state)

        data["chat_settings_repo"].set_field.assert_awaited_once()  # type: ignore[union-attr]


class TestCancelIsNotEatenWhenItIsContent:
    async def test_a_forwarded_cancel_is_treated_as_content(self) -> None:
        """`/cancel` sits at router position 1 with no state filter, so without a
        forward guard it would eat the very input admin_kb carves forwards out
        for: forwarding someone's message to add them as an organizer. aiogram's
        Command filter reads `text or caption` and never looks at forward_origin.
        """
        from src.bot.handlers import admin_kb

        msg, data, bot = _make_message(text="/cancel", forwarded=True)
        state = _FakeState(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=True))
            await _propagate(msg, data, state)

        assert "Отменено." not in _sent_texts(bot), (
            "a forwarded message reading /cancel was treated as the cancel command"
        )
        data["chat_settings_repo"].set_field.assert_awaited_once()  # type: ignore[union-attr]


class TestDemotionDoesNotStrandTheOrganizerPrompt:
    async def test_state_is_cleared_before_the_authority_check(self) -> None:
        """With the clear below the early return, a demoted admin was stuck
        for the lifetime of the process — MemoryStorage has no TTL."""
        from src.bot.handlers import admin_kb

        state = _FakeState(AdminStates.awaiting_kb_organizer)
        await state.update_data(kb_chat_id=-100123, kb_lang="ru")
        msg, data, _bot = _make_message(text="@someone")

        # Patched where admin_kb imported it, not at its definition site: patch
        # the wrong name and the handler takes the authorized path, `clear` is
        # awaited anyway, and the control passes against the un-hoisted code.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(admin_kb, "check_admin_direct", AsyncMock(return_value=False))
            await admin_kb.handle_kb_organizer_add_reply(
                msg,
                data["chat_settings_repo"],  # type: ignore[arg-type]
                data["bot_config_repo"],  # type: ignore[arg-type]
                data["message_repo"],  # type: ignore[arg-type]
                state,  # type: ignore[arg-type]
                data["organizer_names"],  # type: ignore[arg-type]
            )

        assert state.clear_calls == 1
