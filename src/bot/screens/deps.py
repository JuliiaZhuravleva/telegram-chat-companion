"""How a screen entry function reaches the repositories: ``data`` and the container in it.

Every stable entry (`show_hub`, `show_chat`, `show_chats`, `show_rules`) takes
``data``, which is the calling handler's ``**kwargs``. It resolves what it
needs from the request's Dishka container in there, so a caller never has to
know which repositories a screen reads.

The trap this module exists for was measured with a real ``Dispatcher``,
``setup_dishka(auto_inject=True)`` and a startup emit. Dishka's ``inject``
wrapper takes ``dishka_container`` as a named parameter of its own. So a
handler that declares ANY ``FromDishka[...]`` parameter never sees
``dishka_container`` in its ``**kwargs``; a handler that declares none does.
``data=kwargs`` would then work from ``handle_menu`` and raise from
``handle_admin_command``, and a unit test that calls the handler directly with
mocks passes both.

`ContainerAliasMiddleware` copies the container under a second key that the
wrapper does not know, so ``**kwargs`` carries it either way. It is installed
on the main handlers router (`install`). aiogram runs a parent router's inner
middlewares for every nested router's handler, so one installation covers
every screen.

aiogram does the same to its own keys: a handler that declares ``state:
FSMContext`` receives it as that parameter and NOT in ``**kwargs``. An entry
that must disarm a prompt (`show_rules`, `show_stale`) would then find no state
and leave the prompt armed to swallow the admin's next message. The middleware
aliases ``state`` too, and `state_of` reads either key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from aiogram import BaseMiddleware, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject
from dishka import AsyncContainer

T = TypeVar("T")

DISHKA_KEY = "dishka_container"
SCREEN_CONTAINER = "screen_container"
STATE_KEY = "state"
SCREEN_STATE = "screen_state"


class ContainerAliasMiddleware(BaseMiddleware):
    """Copy ``data["dishka_container"]`` to ``data["screen_container"]`` and
    ``data["state"]`` to ``data["screen_state"]``."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        container = data.get(DISHKA_KEY)
        if container is not None:
            data.setdefault(SCREEN_CONTAINER, container)
        state = data.get(STATE_KEY)
        if state is not None:
            data.setdefault(SCREEN_STATE, state)
        return await handler(event, data)


def install(router: Router) -> None:
    """Alias the container for every message and callback handler under ``router``."""
    middleware = ContainerAliasMiddleware()
    router.message.middleware(middleware)
    router.callback_query.middleware(middleware)


def container_of(data: Mapping[str, Any]) -> AsyncContainer:
    """The request container carried by a handler's ``**kwargs``.

    Raises ``LookupError`` rather than returning None: a screen that cannot
    read its data must not render a plausible empty version of itself.
    """
    container = data.get(SCREEN_CONTAINER)
    if container is None:
        container = data.get(DISHKA_KEY)
    if container is None:
        raise LookupError(
            "no Dishka container in data: pass the handler's own **kwargs as data= "
            "(handlers/__init__.py installs ContainerAliasMiddleware on the main router)"
        )
    result: AsyncContainer = container
    return result


def state_of(data: Mapping[str, Any]) -> FSMContext | None:
    """The FSM context carried by a handler's ``**kwargs``, or None.

    None is a legitimate answer (a direct call from a test, an update without
    a user); an entry that gets it simply has no prompt to disarm.
    """
    for key in (SCREEN_STATE, STATE_KEY):
        state = data.get(key)
        if isinstance(state, FSMContext):
            return state
    return None


async def resolve(data: Mapping[str, Any], dependency: type[T]) -> T:
    """``await container_of(data).get(dependency)``."""
    return await container_of(data).get(dependency)
