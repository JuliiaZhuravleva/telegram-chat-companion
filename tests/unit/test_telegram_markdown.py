"""Markdown markers a Telegram client removed are put back from the entities."""

from __future__ import annotations

from aiogram.types import MessageEntity, User

from src.utils.telegram_markdown import entities_to_markdown


def _e(kind: str, offset: int, length: int, **extra: object) -> MessageEntity:
    return MessageEntity(type=kind, offset=offset, length=length, **extra)


def test_no_entities_returns_the_text_unchanged() -> None:
    text = "Ты — бот. Символы *, _ и ` остаются как есть."
    assert entities_to_markdown(text, None) == text
    assert entities_to_markdown(text, []) == text


def test_emphasis_and_code_get_their_markers_back() -> None:
    text = "жирный курсив зачёркнуто код"
    entities = [
        _e("bold", 0, 6),
        _e("italic", 7, 6),
        _e("strikethrough", 14, 10),
        _e("code", 25, 3),
    ]
    assert entities_to_markdown(text, entities) == "**жирный** _курсив_ ~~зачёркнуто~~ `код`"


def test_offsets_are_utf16_so_an_emoji_before_the_entity_does_not_shift_it() -> None:
    # 🔥 is two UTF-16 code units: the bold word starts at 3, not 2.
    assert entities_to_markdown("🔥 огонь", [_e("bold", 3, 5)]) == "🔥 **огонь**"


def test_nested_entities() -> None:
    assert entities_to_markdown("abc", [_e("bold", 0, 3), _e("italic", 1, 1)]) == "**a_b_c**"


def test_code_blocks_keep_their_language() -> None:
    assert entities_to_markdown("x = 1", [_e("pre", 0, 5)]) == "```\nx = 1\n```"
    assert (
        entities_to_markdown("x = 1", [_e("pre", 0, 5, language="python")])
        == "```python\nx = 1\n```"
    )


def test_entities_without_a_markdown_form_keep_only_their_text() -> None:
    text = "ссылка подчёркнуто спойлер Юля"
    entities = [
        _e("text_link", 0, 6, url="https://example.org"),
        _e("underline", 7, 11),
        _e("spoiler", 19, 7),
        _e("text_mention", 27, 3, user=User(id=1, is_bot=False, first_name="Ю")),
    ]
    assert entities_to_markdown(text, entities) == text
