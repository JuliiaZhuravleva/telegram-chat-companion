"""
Unit test configuration.

Unit tests must never touch real databases, APIs, or the network.
All shared fixtures are in the root tests/conftest.py.
"""

from collections.abc import Iterator

import pytest

import src.bot.states.admin as _admin_states


@pytest.fixture(autouse=True)
def _no_prompt_message_on_record() -> Iterator[None]:
    """The record of which message shows a prompt is process-global
    (`src.bot.states.admin._ON_SCREEN`): a test that shows a prompt screen on a
    real message must not leave it for the next test to close."""
    _admin_states._ON_SCREEN.clear()
    yield
    _admin_states._ON_SCREEN.clear()
