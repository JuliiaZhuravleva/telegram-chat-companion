"""Tests for Markdown → Telegram HTML formatter."""

from src.services.text.formatter import markdown_to_html


class TestMarkdownToHtml:
    def test_plain_text_unchanged(self):
        assert markdown_to_html("hello world") == "hello world"

    def test_html_entities_escaped(self):
        assert markdown_to_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_bold_double_asterisk(self):
        assert markdown_to_html("**bold**") == "<b>bold</b>"

    def test_bold_double_underscore(self):
        assert markdown_to_html("__bold__") == "<b>bold</b>"

    def test_italic_single_asterisk(self):
        assert markdown_to_html("*italic*") == "<i>italic</i>"

    def test_italic_single_underscore(self):
        assert markdown_to_html("_italic_") == "<i>italic</i>"

    def test_inline_code(self):
        assert markdown_to_html("`code`") == "<code>code</code>"

    def test_code_block(self):
        result = markdown_to_html("```\nhello\n```")
        assert result == "<pre>hello\n</pre>"

    def test_code_block_with_language(self):
        result = markdown_to_html("```python\nprint('hi')\n```")
        assert result == "<pre>print('hi')\n</pre>"

    def test_strikethrough(self):
        assert markdown_to_html("~~strike~~") == "<s>strike</s>"

    def test_blockquote_single_line(self):
        result = markdown_to_html("> quoted text")
        assert result == "<blockquote>quoted text</blockquote>"

    def test_blockquote_multiple_lines(self):
        result = markdown_to_html("> line one\n> line two")
        assert result == "<blockquote>line one\nline two</blockquote>"

    def test_mixed_formatting(self):
        result = markdown_to_html("**bold** and *italic* and `code`")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result
        assert "<code>code</code>" in result

    def test_code_block_not_processed_for_bold(self):
        result = markdown_to_html("```\n**not bold**\n```")
        assert "<b>" not in result
        assert "**not bold**" in result

    def test_inline_code_not_processed_for_italic(self):
        result = markdown_to_html("`*not italic*`")
        assert "<i>" not in result
        assert "*not italic*" in result

    def test_html_in_code_block_escaped(self):
        result = markdown_to_html("```\n<div>html</div>\n```")
        assert "&lt;div&gt;" in result

    def test_empty_string(self):
        assert markdown_to_html("") == ""

    def test_bold_italic_nested(self):
        result = markdown_to_html("***bold and italic***")
        # Should produce <b><i> or <i><b> — both acceptable
        assert "<b>" in result or "<i>" in result

    def test_blockquote_followed_by_text(self):
        result = markdown_to_html("> quote\nnormal text")
        assert "<blockquote>quote</blockquote>" in result
        assert "normal text" in result

    # -- Headings --

    def test_heading_h1(self):
        assert markdown_to_html("# Title") == "▎<b>Title</b>"

    def test_heading_h2(self):
        assert markdown_to_html("## Subtitle") == "▎<b>Subtitle</b>"

    def test_heading_h3(self):
        assert markdown_to_html("### Section") == "▎<b>Section</b>"

    def test_heading_with_surrounding_text(self):
        result = markdown_to_html("intro\n## Heading\nbody")
        assert "intro" in result
        assert "▎<b>Heading</b>" in result
        assert "body" in result

    def test_heading_not_in_code_block(self):
        result = markdown_to_html("```\n# not a heading\n```")
        assert "▎" not in result

    def test_heading_with_bold_content(self):
        result = markdown_to_html("## **Bold heading**")
        assert "▎<b>" in result

    # -- Lists --

    def test_unordered_list_dash(self):
        assert markdown_to_html("- item one") == "• item one"

    def test_unordered_list_asterisk(self):
        assert markdown_to_html("* item two") == "• item two"

    def test_unordered_list_multiline(self):
        result = markdown_to_html("- first\n- second\n- third")
        assert "• first" in result
        assert "• second" in result
        assert "• third" in result

    def test_numbered_list_unchanged(self):
        text = "1. first\n2. second"
        result = markdown_to_html(text)
        assert "1. first" in result
        assert "2. second" in result

    # -- Horizontal rules --

    def test_horizontal_rule_dashes(self):
        result = markdown_to_html("above\n---\nbelow")
        assert "---" not in result
        assert "above" in result
        assert "below" in result

    def test_horizontal_rule_asterisks(self):
        result = markdown_to_html("above\n***\nbelow")
        assert "***" not in result

    # -- Combined (realistic AI output) --

    def test_summary_like_output(self):
        text = "### Ключевые участники:\n- **Алиса**\n- *Боря*"
        result = markdown_to_html(text)
        assert "▎<b>" in result
        assert "• " in result
        assert "<b>Алиса</b>" in result
        assert "<i>Боря</i>" in result


class TestEmojiMarkdownInteraction:
    """E-2: emoji x markdown_to_html interaction, per the source plan's flagged
    risk (section B) -- ``- item`` becomes ``• item``, so a model line like
    ``- 🔥 тема`` becomes ``• 🔥 тема`` (bullet char immediately followed by an
    emoji "marker"). Verified against real strings, not reasoned about --
    including live-observed shapes from a real cheap-model run (E-2, gpt-5-nano
    / gemini-3-flash-preview, 2026-08-04)."""

    # -- Lists: the exact "double marker" case named in the source plan --

    def test_list_item_with_leading_emoji_gets_single_bullet_prefix(self):
        """The dash->bullet conversion must not duplicate or mangle the emoji:
        exactly one '• ' prefix, emoji preserved immediately after it."""
        assert markdown_to_html("- 🔥 тема") == "• 🔥 тема"

    def test_list_item_leading_emoji_english(self):
        assert markdown_to_html("- 🔥 hot topic") == "• 🔥 hot topic"

    def test_multiple_emoji_list_items_each_get_exactly_one_bullet(self):
        text = "- 🗣 темы\n- 👥 участники\n- ✅ решения\n- ❓ вопросы"
        result = markdown_to_html(text)
        for line in ("🗣 темы", "👥 участники", "✅ решения", "❓ вопросы"):
            assert f"• {line}" in result
        # No line ends up with a doubled '• •' or a stray leading '-'.
        assert "• •" not in result
        assert "\n- " not in result and not result.startswith("- ")

    def test_emoji_after_bullet_with_bold_content(self):
        """Realistic AI shape: '- ✅ **Решено**: детали'."""
        result = markdown_to_html("- ✅ **Решено**: детали")
        assert result == "• ✅ <b>Решено</b>: детали"

    def test_asterisk_list_marker_with_emoji_not_mistaken_for_bold(self):
        """A '*' list marker (not '-') followed by emoji must still resolve as
        a list item, not accidentally get swallowed by the italic/bold regexes."""
        result = markdown_to_html("* 🔥 тема")
        assert result == "• 🔥 тема"
        assert "<i>" not in result
        assert "<b>" not in result

    # -- Headings: emoji inside a heading survives conversion --

    def test_heading_with_leading_emoji(self):
        assert markdown_to_html("## 🗣 Обсуждение") == "▎<b>🗣 Обсуждение</b>"

    def test_heading_with_trailing_emoji(self):
        assert markdown_to_html("## Обсуждение 🗣") == "▎<b>Обсуждение 🗣</b>"

    def test_h1_heading_with_emoji_english(self):
        assert markdown_to_html("# 🚀 Deploy plan") == "▎<b>🚀 Deploy plan</b>"

    # -- Emoji survive HTML-escaping (they are not '<', '>', '&') --

    def test_emoji_not_altered_by_html_escaping(self):
        # Deliberately includes a multi-codepoint (ZWJ) emoji alongside a
        # simple one, to confirm the escaper only touches <, >, & and leaves
        # every other codepoint (including combining sequences) untouched.
        text = "Готово ✅ и семья 👨‍👩‍👧 обсуждают & решают > вопрос"
        result = markdown_to_html(text)
        assert "✅" in result
        assert "👨‍👩‍👧" in result
        assert "&amp;" in result
        assert "&gt;" in result

    # -- Italic/bold boundary regexes don't misfire next to emoji --

    def test_italic_underscore_adjacent_to_emoji(self):
        assert markdown_to_html("_🔥 emphasis_") == "<i>🔥 emphasis</i>"

    def test_italic_asterisk_adjacent_to_emoji(self):
        assert markdown_to_html("*🔥 emphasis*") == "<i>🔥 emphasis</i>"

    def test_bold_adjacent_to_emoji(self):
        assert markdown_to_html("**🔥 важно**") == "<b>🔥 важно</b>"

    # -- Combined realistic AI output block (mirrors live-observed shape) --

    def test_realistic_multi_block_summary_with_emoji(self):
        text = (
            "▎placeholder\n"
            "## 🗣 Темы\n"
            "- 🔥 Конфликт по срокам\n"
            "- ✅ Договорились о дате\n\n"
            "## 👥 Участники\n"
            "- **Алиса** — организатор\n"
            "- *Боря* — исполнитель"
        )
        result = markdown_to_html(text)
        assert "▎<b>🗣 Темы</b>" in result
        assert "• 🔥 Конфликт по срокам" in result
        assert "• ✅ Договорились о дате" in result
        assert "▎<b>👥 Участники</b>" in result
        assert "• <b>Алиса</b> — организатор" in result
        assert "• <i>Боря</i> — исполнитель" in result
        # No raw markdown syntax survives anywhere in the block.
        assert "\n- " not in result
        assert "\n## " not in result
