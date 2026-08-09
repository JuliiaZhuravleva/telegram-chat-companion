"""The command-registry gate: code ↔ registry ↔ Telegram must agree.

This file is the pre-merge half of the check described in
``src/bot/command_registry.py``. CI's ``test`` job is one of the four checks the
production deployer waits on by name, so a failure here stops a drifted command
set from reaching production. Everything is pure — no network, no database.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

from src.bot.command_registry import (
    COMMAND_NAME_RE,
    COMMANDS,
    MAX_DESCRIPTION_LEN,
    SUPPORTED_LANGUAGES,
    CommandScope,
    CommandSpec,
    audit_registry,
    discover_handler_commands,
    specs_for_scope,
    validate_specs,
)
from src.bot.commands import (
    DRIFT_ALERT_KEY,
    LiveCommandDiff,
    _verify_target,
    build_commands,
    detect_stale_scopes,
    notify_command_drift,
    scope_targets,
    sync_and_report,
    sync_bot_commands,
)
from src.bot.filters.admin import IsAdmin
from src.bot.handlers import router as main_router


def fail_only_for_chat_scopes(exc: Exception):
    """A ``delete_my_commands`` side effect that fails for per-chat scopes only.

    A sync can issue two kinds of delete: the per-admin ``BotCommandScopeChat``
    cleanup, and — when something is actually found there — the emptying of the
    global scopes the registry does not manage. A blanket ``side_effect=exc``
    hits both, so a test aiming at the first would silently also assert
    something false about the second — "chat not found" is impossible for a
    scope that has no chat. Keyword-only on purpose: both call sites pass
    ``scope=``/``language_code=`` by name, and a positional call should blow up
    here rather than quietly match.
    """

    def _side_effect(**kwargs: object) -> bool:
        if isinstance(kwargs["scope"], BotCommandScopeChat):
            raise exc
        return True

    return _side_effect


# The commands this bot advertises, per scope. Duplicated from the registry on
# purpose: adding, moving or hiding a command must be a deliberate edit in two
# places, so a stray change to COMMANDS cannot quietly change what users see.
EXPECTED_SCOPES: dict[str, set[str]] = {
    "start": {"private", "admin"},
    "help": {"groups", "private", "admin"},
    "summary": {"groups"},
    "summary500": {"groups"},  # E-2 shortcut: /summary 500 as its own command
    "kb": {"groups", "private", "admin"},
    "remember": set(),  # deliberately hidden — see the spec's hidden_reason
    "admin": {"admin"},
    "settings": {"admin"},
    "costs": {"admin"},
    "panel": {"admin"},  # D-1 shortcut: open a chat's panel by link/title
}


# ---------------------------------------------------------------------------
# Registry ↔ handlers
# ---------------------------------------------------------------------------


def test_every_handled_command_is_in_the_registry() -> None:
    """A command users can type but that is advertised nowhere is drift."""
    audit = audit_registry(main_router)
    assert audit.unregistered == (), (
        f"handled but missing from COMMANDS: {audit.unregistered}. "
        "Add a CommandSpec (with scopes, or empty scopes + hidden_reason)."
    )


def test_every_registry_entry_has_a_handler() -> None:
    """A command advertised to users but implemented nowhere is worse drift."""
    audit = audit_registry(main_router)
    assert audit.orphaned == (), (
        f"advertised in COMMANDS but no handler: {audit.orphaned}. "
        "Either implement it or drop the spec."
    )


def test_admin_gating_matches_advertised_scopes() -> None:
    audit = audit_registry(main_router)
    assert audit.admin_mismatch == (), "\n".join(audit.admin_mismatch)


def test_registry_audit_is_clean() -> None:
    assert audit_registry(main_router).ok


def test_scope_map_matches_expectation() -> None:
    """Snapshot: which command shows up where."""
    actual = {spec.command: {scope.value for scope in spec.scopes} for spec in COMMANDS}
    assert actual == EXPECTED_SCOPES


def test_discovery_finds_the_known_handlers() -> None:
    """Guards the introspection itself: if aiogram changes how filters are
    stored, discovery would silently return {} and every audit above would
    pass vacuously."""
    found = discover_handler_commands(main_router)
    assert set(found) >= {"start", "help", "summary", "kb", "remember", "admin", "costs", "panel"}
    assert found["admin"].admin_gated is True
    assert found["start"].admin_gated is False
    # /summary has two handlers (group + DM), both discovered
    assert len(found["summary"].handlers) == 2


# ---------------------------------------------------------------------------
# Registry self-consistency
# ---------------------------------------------------------------------------


def test_specs_are_valid() -> None:
    violations = validate_specs()
    assert violations == (), "\n".join(f"{v.command}: {v.problem}" for v in violations)


@pytest.mark.parametrize("spec", COMMANDS, ids=lambda s: s.command)
def test_spec_shape(spec: CommandSpec) -> None:
    assert COMMAND_NAME_RE.match(spec.command)
    for lang in SUPPORTED_LANGUAGES:
        assert spec.description.get(lang), f"{spec.command}: no {lang} description"
        assert len(spec.description[lang]) <= MAX_DESCRIPTION_LEN
    if not spec.scopes:
        assert spec.hidden_reason, f"{spec.command} is hidden without a reason"


def test_hidden_commands_are_reported_with_their_reason() -> None:
    audit = audit_registry(main_router)
    assert any("remember" in entry for entry in audit.hidden)
    # Informational only — a documented hidden command is not a problem.
    assert audit.ok


# ---------------------------------------------------------------------------
# Negative controls — the audit must actually fail on drift
# ---------------------------------------------------------------------------


def _router_with(*handler_filters: object) -> Router:
    router = Router(name="probe")

    async def handler(_message: object) -> None: ...

    router.message.register(handler, *handler_filters)  # type: ignore[arg-type]
    return router


def test_audit_flags_an_unregistered_command() -> None:
    """Control: a handler for a command with no spec must be reported."""
    audit = audit_registry(_router_with(Command("totally_new")))
    assert "totally_new" in audit.unregistered
    assert not audit.ok


def test_audit_flags_an_orphaned_command() -> None:
    """Control: every spec is orphaned against a router with no handlers."""
    audit = audit_registry(Router(name="empty"))
    assert set(audit.orphaned) == {spec.command for spec in COMMANDS}
    assert not audit.ok


def test_audit_flags_admin_gated_command_advertised_publicly() -> None:
    """Control: /costs is IsAdmin-gated; a router where it is public still
    trips the mismatch check because the spec advertises ADMIN only — so we
    invert it: an ungated handler for an admin_only spec must be reported."""
    audit = audit_registry(_router_with(Command("costs")))  # no IsAdmin
    assert any("costs" in entry and "admin_only" in entry for entry in audit.admin_mismatch)
    assert not audit.ok


def test_validate_specs_flags_a_hidden_command_without_a_reason() -> None:
    broken = (replace(COMMANDS[0], scopes=frozenset(), hidden_reason=None),)
    violations = validate_specs(broken)
    assert any("hidden_reason" in v.problem for v in violations)


def test_validate_specs_flags_an_over_long_description() -> None:
    broken = (replace(COMMANDS[0], description={"ru": "x" * 300, "en": "y"}),)
    violations = validate_specs(broken)
    assert any("256" in v.problem for v in violations)


def test_validate_specs_flags_a_bad_command_name() -> None:
    broken = (replace(COMMANDS[0], command="Not A Command"),)
    violations = validate_specs(broken)
    assert any("Bot API limit" in v.problem for v in violations)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_build_commands_renders_the_scope_in_declaration_order() -> None:
    groups = build_commands(CommandScope.GROUPS, "ru")
    assert [c.command for c in groups] == [s.command for s in specs_for_scope(CommandScope.GROUPS)]
    assert all(isinstance(c, BotCommand) for c in groups)
    assert all(c.description for c in groups)


def test_language_less_variant_uses_english() -> None:
    """The fallback list every non-ru/en client resolves to."""
    fallback = {c.command: c.description for c in build_commands(CommandScope.PRIVATE, None)}
    english = {c.command: c.description for c in build_commands(CommandScope.PRIVATE, "en")}
    assert fallback == english


def test_scope_targets_cover_globals_plus_one_per_admin() -> None:
    targets = scope_targets([111, 222])
    assert [t.label for t in targets] == ["groups", "private", "admin:111", "admin:222"]


# ---------------------------------------------------------------------------
# Live diff and push behaviour (mocked Bot)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_target_reports_no_diff_when_in_sync() -> None:
    """False-positive control: an in-sync bot must produce an empty diff, or
    every 'drift detected' below would prove nothing."""
    target = scope_targets([])[1]  # private
    bot = MagicMock()
    bot.get_my_commands = AsyncMock(
        # Keyword-faithful: _verify_target calls get_my_commands(scope=…,
        # language_code=…), and a lambda that cannot accept those names raises
        # TypeError — which this control would otherwise read as "no diff".
        side_effect=lambda **kw: build_commands(target.scope, kw["language_code"])
    )
    diffs, unverified = await _verify_target(bot, target)
    assert diffs == []
    assert unverified == []


@pytest.mark.asyncio
async def test_failed_readback_is_reported_not_swallowed() -> None:
    """A scope we could not read produces no diff — byte-identical to a clean
    scope. Silently continuing (the first version did) let a rate limit render
    as "✓ registry, handlers and Telegram agree": absence of evidence must
    never be the approval condition."""
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(return_value=True)
    bot.get_my_commands = AsyncMock(side_effect=OSError("connection reset"))

    report = await sync_bot_commands(bot, [])

    assert report.unverified, "a read-back that never happened must be recorded"
    assert not report.ok
    assert any("NOT verified" in problem for problem in report.problems())


@pytest.mark.asyncio
async def test_verify_target_reports_missing_and_unexpected() -> None:
    target = scope_targets([])[1]  # private
    bot = MagicMock()
    bot.get_my_commands = AsyncMock(
        return_value=[BotCommand(command="ghost", description="left over")]
    )
    diffs, unverified = await _verify_target(bot, target)
    assert unverified == []
    assert diffs, "a bot serving a completely different list must be flagged"
    diff = diffs[0]
    assert "ghost" in diff.unexpected
    assert "help" in diff.missing
    assert "private" in diff.describe()


@pytest.mark.asyncio
async def test_verify_target_reports_a_changed_description() -> None:
    target = scope_targets([])[1]
    stale = [
        BotCommand(command=c.command, description="stale text")
        for c in build_commands(target.scope, None)
    ]
    bot = MagicMock()
    bot.get_my_commands = AsyncMock(return_value=stale)
    diffs, _ = await _verify_target(bot, target)
    assert diffs[0].changed


@pytest.mark.asyncio
async def test_push_covers_every_language_variant_including_the_fallback() -> None:
    """The locale-fallback fix: without a language-less push, a client on a
    third locale resolves to nothing at all."""
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(return_value=True)
    bot.get_my_commands = AsyncMock(return_value=[])

    await sync_bot_commands(bot, [], verify=False)

    languages_per_scope: dict[str, set[str | None]] = {}
    for call in bot.set_my_commands.await_args_list:
        scope = type(call.kwargs["scope"]).__name__
        languages_per_scope.setdefault(scope, set()).add(call.kwargs["language_code"])
    assert languages_per_scope, "nothing was pushed"
    for scope, languages in languages_per_scope.items():
        assert languages == {None, "ru", "en"}, f"{scope} missing a variant: {languages}"


@pytest.mark.asyncio
async def test_stale_admin_scopes_are_deleted_and_the_list_is_updated() -> None:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=[111, 999])
    repo.set = AsyncMock()
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(return_value=True)
    bot.get_my_commands = AsyncMock(return_value=[])
    bot.delete_my_commands = AsyncMock(return_value=True)

    report = await sync_bot_commands(bot, [111], bot_config_repo=repo, verify=False)

    # A sync also empties the unmanaged global scopes, so filter to the per-chat
    # deletes this test is actually about rather than reading .chat_id off all
    # of them (which raises on a global scope and reads as a failure here).
    chat_deletes = [
        call
        for call in bot.delete_my_commands.await_args_list
        if isinstance(call.kwargs["scope"], BotCommandScopeChat)
    ]
    assert {call.kwargs["scope"].chat_id for call in chat_deletes} == {999}
    # every language variant, or the ex-admin keeps the ru/en menu
    assert len(chat_deletes) == 3
    assert report.deleted_scopes == (999,)
    repo.set.assert_awaited()
    assert repo.set.await_args.args[1] == [111]


@pytest.mark.asyncio
async def test_unreachable_stale_scope_is_dropped_not_retried_forever() -> None:
    """Found in live testing: a stale id Telegram refuses to address ("chat not
    found") used to be recorded as a delete *failure*, so it stayed in the
    stored list and reported drift on every single restart — a permanent false
    alarm from one mistyped admin id. An unaddressable chat cannot be holding a
    menu, so it counts as cleaned."""
    repo = MagicMock()
    repo.get = AsyncMock(return_value=[111, 999])
    repo.set = AsyncMock()
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(return_value=True)
    bot.get_my_commands = AsyncMock(return_value=[])
    bot.delete_my_commands = AsyncMock(
        side_effect=fail_only_for_chat_scopes(
            TelegramBadRequest(method=MagicMock(), message="chat not found")
        )
    )

    report = await sync_bot_commands(bot, [111], bot_config_repo=repo, verify=False)

    assert report.stale_scopes == (), "an unaddressable scope must not linger"
    assert report.deleted_scopes == (999,)
    assert repo.set.await_args.args[1] == [111]
    assert report.ok


@pytest.mark.asyncio
async def test_transport_error_keeps_the_stale_scope_for_a_retry() -> None:
    """The other half of the control above: a network blip is NOT evidence the
    scope is gone, so the id must survive for the next run."""
    repo = MagicMock()
    repo.get = AsyncMock(return_value=[111, 999])
    repo.set = AsyncMock()
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(return_value=True)
    bot.get_my_commands = AsyncMock(return_value=[])
    bot.delete_my_commands = AsyncMock(
        side_effect=fail_only_for_chat_scopes(OSError("connection reset"))
    )

    report = await sync_bot_commands(bot, [111], bot_config_repo=repo, verify=False)

    assert report.stale_scopes == (999,)
    assert report.deleted_scopes == ()
    assert repo.set.await_args.args[1] == [111, 999]
    assert not report.ok


@pytest.mark.asyncio
async def test_unreachable_admin_scope_is_reported_once_not_per_language() -> None:
    """An admin who never started the bot fails all three pushes; the alert
    should say so once, in words, not repeat the raw API string."""
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="chat not found")
    )
    bot.get_my_commands = AsyncMock(return_value=[])

    report = await sync_bot_commands(bot, [777], verify=False)

    admin_failures = [f for f in report.push_failures if "admin:777" in f]
    assert len(admin_failures) == 1
    assert "never" in admin_failures[0]
    assert "admin:777" not in report.pushed


@pytest.mark.asyncio
async def test_read_only_run_reports_stale_scopes_without_touching_telegram() -> None:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=[111, 999])
    repo.set = AsyncMock()
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    bot.delete_my_commands = AsyncMock()
    bot.get_my_commands = AsyncMock(
        side_effect=lambda scope, language_code: build_commands(
            CommandScope.PRIVATE if "Private" in type(scope).__name__ else CommandScope.GROUPS,
            language_code,
        )
    )

    report = await sync_bot_commands(bot, [111], bot_config_repo=repo, push=False)

    bot.set_my_commands.assert_not_awaited()
    bot.delete_my_commands.assert_not_awaited()
    repo.set.assert_not_awaited()
    assert report.stale_scopes == (999,)
    assert not report.ok


@pytest.mark.asyncio
async def test_detect_stale_scopes_parses_a_json_string() -> None:
    """asyncpg hands JSONB back as a str often enough to matter."""
    repo = MagicMock()
    repo.get = AsyncMock(return_value="[111, 222]")
    assert await detect_stale_scopes([222], repo) == [111]


# ---------------------------------------------------------------------------
# Background startup routine
# ---------------------------------------------------------------------------


def _repo_with_store(store: dict[str, object]) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(side_effect=lambda key: store.get(key))
    repo.set = AsyncMock(side_effect=lambda key, value, **_kw: store.__setitem__(key, value))
    return repo


@pytest.mark.asyncio
async def test_sync_and_report_never_raises_on_a_dead_api() -> None:
    """It runs as a fire-and-forget task, so an exception escaping it would be
    an unhandled task exception, not a startup failure anyone sees."""
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=RuntimeError("Telegram is down"))
    bot.get_my_commands = AsyncMock(side_effect=RuntimeError("Telegram is down"))
    bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram is down"))

    report = await sync_and_report(bot, [111], bot_config_repo=_repo_with_store({}))

    assert not report.ok
    assert report.problems()


@pytest.mark.asyncio
async def test_sync_and_report_is_cancellable_mid_flight() -> None:
    """Shutdown cancels the task; that must propagate (CancelledError is a
    BaseException, so the module's broad `except Exception` must not eat it)
    rather than leave the loop waiting on a hung Telegram call."""
    started = asyncio.Event()

    async def _hang(*_args: object, **_kwargs: object) -> None:
        started.set()
        await asyncio.sleep(3600)

    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=_hang)
    bot.get_my_commands = AsyncMock(return_value=[])
    bot.send_message = AsyncMock()

    task = asyncio.ensure_future(sync_and_report(bot, [111], bot_config_repo=_repo_with_store({})))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_sync_and_report_alerts_and_returns_the_report() -> None:
    store: dict[str, object] = {}
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(return_value=True)
    bot.get_my_commands = AsyncMock(return_value=[])  # nothing stored → drift
    bot.send_message = AsyncMock()

    report = await sync_and_report(bot, [111], bot_config_repo=_repo_with_store(store))

    assert not report.ok
    bot.send_message.assert_awaited()
    assert store[DRIFT_ALERT_KEY], "the digest must be recorded so a restart does not re-alert"


# ---------------------------------------------------------------------------
# Drift alert
# ---------------------------------------------------------------------------


def _drifted_report() -> object:
    from src.bot.commands import CommandSyncReport

    return CommandSyncReport(
        live_diffs=(LiveCommandDiff(target="private", language=None, missing=("help",)),)
    )


@pytest.mark.asyncio
async def test_drift_alert_is_sent_once_per_distinct_problem_set() -> None:
    report = _drifted_report()
    stored: dict[str, object] = {}
    repo = MagicMock()
    repo.get = AsyncMock(side_effect=lambda key: stored.get(key))
    repo.set = AsyncMock(side_effect=lambda key, value, **_kw: stored.__setitem__(key, value))
    bot = MagicMock()
    bot.send_message = AsyncMock()

    assert await notify_command_drift(bot, report, [111], repo) is True  # type: ignore[arg-type]
    assert bot.send_message.await_count == 1

    # A restart with the same unfixed drift must not DM again.
    assert await notify_command_drift(bot, report, [111], repo) is False  # type: ignore[arg-type]
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_drift_alert_resets_once_the_drift_is_gone() -> None:
    from src.bot.commands import DRIFT_ALERT_KEY, CommandSyncReport

    stored: dict[str, object] = {DRIFT_ALERT_KEY: "olddigest"}
    repo = MagicMock()
    repo.get = AsyncMock(side_effect=lambda key: stored.get(key))
    repo.set = AsyncMock(side_effect=lambda key, value, **_kw: stored.__setitem__(key, value))
    bot = MagicMock()
    bot.send_message = AsyncMock()

    sent = await notify_command_drift(bot, CommandSyncReport(), [111], repo)

    assert sent is False
    bot.send_message.assert_not_awaited()
    assert stored[DRIFT_ALERT_KEY] is None


@pytest.mark.asyncio
async def test_drift_alert_survives_a_send_failure() -> None:
    report = _drifted_report()
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    repo.set = AsyncMock()
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("chat not found"))

    assert await notify_command_drift(bot, report, [111], repo) is False  # type: ignore[arg-type]
    # Nothing recorded, so the next start retries the alert.
    repo.set.assert_not_awaited()


def test_render_text_stays_within_telegrams_message_limit() -> None:
    """A mangled admin_ids list yields one problem per admin; an over-long body
    is rejected outright, which would lose the alert entirely."""
    from src.bot.commands import CommandSyncReport

    report = CommandSyncReport(
        push_failures=tuple(
            f"admin:{100000000 + i}: Telegram cannot address this chat (chat not found) — "
            "either the id in admin_ids is wrong, or that admin has never started a chat"
            for i in range(200)
        )
    )
    text = report.render_text("ru")
    assert len(text) < 4096
    assert "и ещё" in text, "the truncated remainder must still be counted"


def test_render_text_wraps_bare_ids_in_code() -> None:
    """Telegram auto-links bare 9-11 digit integers as dead 'phone' links."""
    from src.bot.commands import CommandSyncReport

    report = CommandSyncReport(stale_scopes=(123456789,))
    text = report.render_text("en")
    assert "<code>123456789</code>" in text


def test_render_text_escapes_before_wrapping_ids() -> None:
    """Control: markup inside a Telegram error string must not survive, even
    though _escape_alert_line deliberately adds <code> tags of its own."""
    from src.bot.commands import CommandSyncReport

    report = CommandSyncReport(push_failures=("<b>evil</b> 123456789",))
    text = report.render_text("en")
    assert "<b>evil</b>" not in text
    assert "&lt;b&gt;evil&lt;/b&gt;" in text


def test_render_text_escapes_and_names_the_repair_command() -> None:
    report = _drifted_report()
    text = report.render_text("ru")  # type: ignore[attr-defined]
    assert "scripts.verify_commands" in text
    assert "help" in text
    assert not re.search(r"<(?!/?(b|i|code)>)", text), "only b/i/code markup may reach Telegram"


# ---------------------------------------------------------------------------
# Guard against the filter-introspection assumption
# ---------------------------------------------------------------------------


def test_isadmin_is_detected_as_a_filter_instance() -> None:
    """discover_handler_commands relies on IsAdmin being a plain instance in
    the filter list; a refactor to a factory function would silently disable
    every admin cross-check above."""
    router = _router_with(Command("probe"), IsAdmin())
    found = discover_handler_commands(router)
    assert found["probe"].admin_gated is True


def test_chat_type_filters_do_not_break_discovery() -> None:
    router = _router_with(Command("probe"), F.chat.type == "private")
    found = discover_handler_commands(router)
    assert "probe" in found
    assert found["probe"].admin_gated is False


# ---------------------------------------------------------------------------
# Shadowing scopes: the class of bug that is invisible to a per-scope diff
# ---------------------------------------------------------------------------


def _bot_with_scopes(shadow: dict[str, list[str]] | None = None) -> MagicMock:
    """A bot whose managed scopes are perfectly in sync.

    ``shadow`` seeds the *unmanaged* global scopes, keyed by label, with the
    language-less variant only — which is how the real leftover looked: an
    older deploy set it once, without a language, and every locale falls back
    to it inside that scope.
    """
    shadow = shadow or {}
    scope_map = {
        BotCommandScopeAllGroupChats: CommandScope.GROUPS,
        BotCommandScopeAllPrivateChats: CommandScope.PRIVATE,
        BotCommandScopeChat: CommandScope.ADMIN,
    }

    def _get(**kwargs: object) -> list[BotCommand]:
        scope = kwargs["scope"]
        language = kwargs["language_code"]
        for scope_type, registry_scope in scope_map.items():
            if isinstance(scope, scope_type):
                return build_commands(registry_scope, language)
        label = (
            "all_chat_administrators"
            if isinstance(scope, BotCommandScopeAllChatAdministrators)
            else "default"
        )
        if language is None:
            return [BotCommand(command=name, description=name) for name in shadow.get(label, [])]
        return []

    bot = MagicMock()
    bot.set_my_commands = AsyncMock(return_value=True)
    bot.delete_my_commands = AsyncMock(return_value=True)
    bot.get_my_commands = AsyncMock(side_effect=_get)
    return bot


@pytest.mark.asyncio
async def test_stale_all_chat_administrators_scope_is_reported() -> None:
    """The production defect of 2026-08-08, reproduced.

    Every managed scope agreed with the registry — `all_group_chats` carried
    `/kb` in all three languages and the read-back was clean — yet no chat
    administrator ever saw `/kb`, because a list left by a pre-registry version
    sat one level more specific and Telegram never merges scopes. The old
    verifier printed "registry, handlers and Telegram agree" throughout, since
    it only ever looked at the scopes it manages.
    """
    bot = _bot_with_scopes({"all_chat_administrators": ["help", "summary"]})

    report = await sync_bot_commands(bot, [], push=False)

    assert not report.ok, "a scope that outranks every managed one must not read as clean"
    assert any("all_chat_administrators" in problem for problem in report.problems())
    assert any("shadowing scope" in problem for problem in report.problems())
    # The managed scopes really were fine — otherwise this test would pass for
    # the wrong reason, flagging ordinary drift instead of the shadow.
    assert report.live_diffs == ()


@pytest.mark.asyncio
async def test_clean_bot_reports_no_shadowing() -> None:
    """False-positive control. Without it the assertion above proves only that
    *something* is reported, not that an empty scope stays quiet."""
    report = await sync_bot_commands(_bot_with_scopes(), [], push=False)

    assert report.shadow_scopes == ()
    assert report.ok, report.problems()


def _unmanaged_deletes(bot: MagicMock) -> set[tuple[str, str | None]]:
    return {
        (
            "all_chat_administrators"
            if isinstance(call.kwargs["scope"], BotCommandScopeAllChatAdministrators)
            else "default",
            call.kwargs["language_code"],
        )
        for call in bot.delete_my_commands.await_args_list
        if isinstance(
            call.kwargs["scope"], BotCommandScopeAllChatAdministrators | BotCommandScopeDefault
        )
    }


@pytest.mark.asyncio
async def test_sync_empties_the_unmanaged_global_scopes() -> None:
    """Reporting is not enough: a deploy has to converge production without a
    human running anything by hand.

    Exactly the variants that hold something are deleted. The others are already
    empty, so deleting them would be six API calls per startup, forever, to
    re-empty nothing — against this module's own bounded-read-back principle.
    """
    bot = _bot_with_scopes({"default": ["help", "summary"]})

    report = await sync_bot_commands(bot, [], verify=False)

    assert _unmanaged_deletes(bot) == {("default", None)}
    assert report.shadow_scopes == (), "it was cleared, so nothing is still shadowing"
    assert any("cleared shadowing scope" in note for note in report.notes)
    assert report.ok


@pytest.mark.asyncio
async def test_clean_bot_issues_no_unmanaged_deletes() -> None:
    """The cost half of the control above. Read-then-delete is only worth its
    complexity if the steady state really is read-only."""
    bot = _bot_with_scopes()

    await sync_bot_commands(bot, [], verify=False)

    assert _unmanaged_deletes(bot) == set()


@pytest.mark.asyncio
async def test_failed_clear_keeps_reporting_the_shadow() -> None:
    """A delete that failed leaves users looking at the wrong menu. Reporting
    only "a call failed" would lose that; the scope must stay in shadow_scopes."""
    bot = _bot_with_scopes({"all_chat_administrators": ["help", "summary"]})
    bot.delete_my_commands = AsyncMock(side_effect=OSError("connection reset"))

    report = await sync_bot_commands(bot, [], verify=False)

    assert any("all_chat_administrators" in entry for entry in report.shadow_scopes)
    assert any("delete failed" in entry for entry in report.shadow_scopes), (
        "the cause belongs on the shadow line, not in a second problem for the same condition"
    )
    # One condition, one problem line — not also a "push failed: …" entry.
    assert len([p for p in report.problems() if "all_chat_administrators" in p]) == 1
    assert not report.ok


@pytest.mark.asyncio
async def test_unreadable_unmanaged_scope_is_unverified_not_clean() -> None:
    """Same rule as everywhere else here: a scope we could not read is UNKNOWN.
    An empty list and a failed call look identical, so the failure is recorded
    rather than allowed to render as "nothing is shadowing"."""
    bot = _bot_with_scopes()

    def _get(**kwargs: object) -> list[BotCommand]:
        scope = kwargs["scope"]
        if isinstance(scope, BotCommandScopeAllChatAdministrators | BotCommandScopeDefault):
            raise OSError("connection reset")
        return build_commands(CommandScope.GROUPS, kwargs["language_code"])

    bot.get_my_commands = AsyncMock(side_effect=_get)

    report = await sync_bot_commands(bot, [], push=False)

    assert report.shadow_scopes == ()
    assert report.unverified, "a read-back that never happened must be recorded"
    assert not report.ok
