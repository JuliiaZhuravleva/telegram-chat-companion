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
        text = "### Ключевые участники:\n- **Планшет**\n- *Вишенки*"
        result = markdown_to_html(text)
        assert "▎<b>" in result
        assert "• " in result
        assert "<b>Планшет</b>" in result
        assert "<i>Вишенки</i>" in result
