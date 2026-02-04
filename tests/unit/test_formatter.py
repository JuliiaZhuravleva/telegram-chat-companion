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
