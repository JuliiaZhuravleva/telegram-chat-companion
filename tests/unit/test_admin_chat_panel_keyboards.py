"""Tests for the chat settings panel's keyboards (B-1, ADR-0006; grouped
navigation, B-2, ADR-0010)."""

from __future__ import annotations

from dataclasses import replace

from src.bot.keyboards.admin_chat_panel import (
    _format_value,
    chat_panel_group_keyboard,
    chat_panel_picker_keyboard,
    chat_panel_root_keyboard,
)
from src.bot.settings_fields import FIELDS_BY_KEY, FieldGroup, group_label
from src.models.chat_config import ChatConfig

CHAT_ID = -1001234567890


def _cfg(**overrides) -> ChatConfig:
    """Effective config for the root screen's per-group status text (B-3)."""
    return replace(ChatConfig(chat_id=CHAT_ID), **overrides)


def _get_callbacks(keyboard):
    return [
        btn.callback_data for row in keyboard.inline_keyboard for btn in row if btn.callback_data
    ]


def _get_labels(keyboard):
    return [btn.text for row in keyboard.inline_keyboard for btn in row]


def _row_for(keyboard, callback_data: str):
    for row in keyboard.inline_keyboard:
        for btn in row:
            if btn.callback_data == callback_data:
                return btn
    return None


class TestChatPanelPickerKeyboard:
    def test_one_row_per_chat_with_menu_callback(self):
        chats = [
            {"chat_id": 111, "chat_title": "Alpha"},
            {"chat_id": 222, "chat_title": "Beta"},
        ]
        kb = chat_panel_picker_keyboard(chats, lang="ru", page=0, total=2, per_page=10)
        callbacks = _get_callbacks(kb)
        assert "adm_pnl_menu:ru:111" in callbacks
        assert "adm_pnl_menu:ru:222" in callbacks

    def test_back_button_goes_to_main_menu(self):
        kb = chat_panel_picker_keyboard([], lang="ru", page=0, total=0, per_page=10)
        callbacks = _get_callbacks(kb)
        assert "adm_menu:ru" in callbacks

    def test_pagination_uses_own_prefix(self):
        chats = [{"chat_id": i, "chat_title": f"Chat {i}"} for i in range(10)]
        kb = chat_panel_picker_keyboard(chats, lang="ru", page=0, total=25, per_page=10)
        callbacks = _get_callbacks(kb)
        assert "adm_pnl:ru:1" in callbacks

    def test_shows_message_count_in_caption_when_present(self):
        """C-1: counter in the caption, so the activity order doesn't look
        arbitrary."""
        chats = [{"chat_id": 111, "chat_title": "Alpha", "message_count_24h": 42}]
        kb = chat_panel_picker_keyboard(chats, lang="ru", page=0, total=1, per_page=10)
        labels = _get_labels(kb)
        assert "Alpha · 42" in labels

    def test_shows_zero_count_explicitly(self):
        """A zero count still renders (not hidden) -- it's the tie-break
        signal, not an id, so no autolink concern."""
        chats = [{"chat_id": 111, "chat_title": "Quiet", "message_count_24h": 0}]
        kb = chat_panel_picker_keyboard(chats, lang="ru", page=0, total=1, per_page=10)
        labels = _get_labels(kb)
        assert "Quiet · 0" in labels

    def test_omits_count_suffix_when_key_absent(self):
        """Callers that don't opt into activity sorting (none currently, but
        the keyboard must stay backward-compatible) get a bare title."""
        chats = [{"chat_id": 111, "chat_title": "Alpha"}]
        kb = chat_panel_picker_keyboard(chats, lang="ru", page=0, total=1, per_page=10)
        labels = _get_labels(kb)
        assert "Alpha" in labels
        assert not any("·" in label for label in labels)


class TestFormatValue:
    def test_str_list_joins_items(self):
        field = FIELDS_BY_KEY["trigger_words"]
        assert _format_value(field, ("bot", "бот")) == "bot, бот"

    def test_str_list_empty_placeholder(self):
        field = FIELDS_BY_KEY["trigger_words"]
        assert _format_value(field, ()) == "—"

    def test_float_uses_general_format(self):
        field = FIELDS_BY_KEY["random_response_chance"]
        assert _format_value(field, 0.05) == "0.05"

    def test_int_as_plain_string(self):
        field = FIELDS_BY_KEY["random_response_min_interval"]
        assert _format_value(field, 300) == "300"

    def test_str_empty_placeholder(self):
        field = FIELDS_BY_KEY["system_prompt"]
        assert _format_value(field, "") == "—"

    def test_str_truncated_with_ellipsis(self):
        field = FIELDS_BY_KEY["system_prompt"]
        text = _format_value(field, "x" * 100)
        assert text.endswith("…")
        assert len(text) == 40


class TestChatPanelRootKeyboard:
    """ADR-0010 Decisions 1 and 3: section list, not a flat field scroll."""

    def test_group_buttons_link_to_group_screens(self):
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=False,
            reactions_status=(False, True),
        )
        callbacks = _get_callbacks(kb)
        for group in (
            FieldGroup.BEHAVIOR,
            FieldGroup.MODULES,
            FieldGroup.STICKERS,
            FieldGroup.RULES,
        ):
            assert f"adm_pnl_grp:ru:{CHAT_ID}:{group.value}" in callbacks

    def test_group_buttons_start_with_their_label(self):
        """B-3 appends a status suffix; the label still leads the button."""
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=False,
            reactions_status=(False, True),
        )
        labels = _get_labels(kb)
        assert any(text.startswith(group_label(FieldGroup.MODULES, "ru")) for text in labels)

    def test_no_individual_field_rows_on_root(self):
        """The old flat list is gone -- no field's toggle/tolerance callback
        shows up on the root screen, only the 4 group buttons."""
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=False,
            reactions_status=(False, True),
        )
        callbacks = _get_callbacks(kb)
        assert not any(cb.startswith("adm_pnl_tgl:") for cb in callbacks)
        assert not any(cb.startswith("adm_pnl_tol:") for cb in callbacks)

    def test_kb_group_renders_link_row_not_a_toggle(self):
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=True,
            reactions_status=(False, True),
        )
        btn = _row_for(kb, f"adm_kb_menu:ru:{CHAT_ID}")
        assert btn is not None
        assert "✅" in btn.text
        # kb_enabled must never be reachable through the group-screen prefix.
        assert not any(
            (cb or "").startswith("adm_pnl_grp:") and cb.endswith(":kb")
            for cb in _get_callbacks(kb)
        )

    def test_reactions_group_renders_combined_link_row(self):
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=False,
            reactions_status=(True, False),
        )
        btn = _row_for(kb, f"adm_react_menu:ru:{CHAT_ID}")
        assert btn is not None
        assert btn.text.count("✅") == 1
        assert btn.text.count("⚫") == 1

    def test_back_button_returns_to_picker(self):
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=False,
            reactions_status=(False, True),
        )
        assert kb.inline_keyboard[-1][0].callback_data == "adm_pnl:ru:0"

    def test_english_labels(self):
        kb = chat_panel_root_keyboard(
            "en",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=False,
            reactions_status=(False, True),
        )
        labels = _get_labels(kb)
        assert any(text.startswith(group_label(FieldGroup.MODULES, "en")) for text in labels)
        # B-3's status suffix must be localized too, not left in Russian.
        assert any("on 0/8" in text or "on " in text for text in labels)
        assert not any("вкл" in text or "настро" in text for text in labels)

    def test_seven_rows_total(self):
        """4 group buttons + KB link + Reactions link + back (ADR-0010 Decision 3)."""
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(),
            kb_status=False,
            reactions_status=(False, True),
        )
        assert len(kb.inline_keyboard) == 7


class TestChatPanelGroupKeyboard:
    """ADR-0010 Decision 4: one screen per field-owning group."""

    def _config(self, **overrides) -> ChatConfig:
        return replace(ChatConfig(chat_id=CHAT_ID), **overrides)

    def test_bool_field_gets_toggle_row(self):
        config = self._config(rag_enabled=True)
        kb = chat_panel_group_keyboard(
            "ru", chat_id=CHAT_ID, group=FieldGroup.MODULES, config=config, row=None
        )
        btn = _row_for(kb, f"adm_pnl_tgl:ru:{CHAT_ID}:rag")
        assert btn is not None
        assert "✅" in btn.text

    def test_non_bool_field_is_read_only_noop(self):
        config = self._config(system_prompt="Be nice")
        kb = chat_panel_group_keyboard(
            "ru", chat_id=CHAT_ID, group=FieldGroup.BEHAVIOR, config=config, row=None
        )
        labels = _get_labels(kb)
        assert any("Be nice" in label for label in labels)
        # system_prompt's row must not be wired to the generic toggle handler.
        sp_rows = [btn for row in kb.inline_keyboard for btn in row if "Be nice" in btn.text]
        assert all(btn.callback_data == "noop" for btn in sp_rows)

    def test_tolerance_field_gets_dedicated_edit_flow(self):
        config = self._config(tolerance_level=0.5)
        kb = chat_panel_group_keyboard(
            "ru", chat_id=CHAT_ID, group=FieldGroup.STICKERS, config=config, row=None
        )
        assert _row_for(kb, f"adm_pnl_tol:ru:{CHAT_ID}") is not None

    def test_scoped_to_requested_group_only(self):
        """A MODULES screen must not leak BEHAVIOR/STICKERS/RULES fields."""
        config = self._config(rag_enabled=True, system_prompt="Be nice")
        kb = chat_panel_group_keyboard(
            "ru", chat_id=CHAT_ID, group=FieldGroup.MODULES, config=config, row=None
        )
        labels = _get_labels(kb)
        assert not any("Be nice" in label for label in labels)

    def test_back_button_returns_to_root_menu(self):
        config = self._config()
        kb = chat_panel_group_keyboard(
            "ru", chat_id=CHAT_ID, group=FieldGroup.RULES, config=config, row=None
        )
        assert kb.inline_keyboard[-1][0].callback_data == f"adm_pnl_menu:ru:{CHAT_ID}"

    def test_english_labels(self):
        config = self._config()
        kb = chat_panel_group_keyboard(
            "en", chat_id=CHAT_ID, group=FieldGroup.MODULES, config=config, row=None
        )
        labels = _get_labels(kb)
        assert any(label.startswith("RAG memory") for label in labels)

    def test_row_count_matches_group_field_count_plus_back(self):
        """MODULES (largest group) is 8 field rows + 1 back row (ADR-0010
        Decision 4's row-count check)."""
        config = self._config()
        kb = chat_panel_group_keyboard(
            "ru", chat_id=CHAT_ID, group=FieldGroup.MODULES, config=config, row=None
        )
        assert len(kb.inline_keyboard) == 9


class TestRootGroupStatus:
    """B-3: the per-group status suffix on the root screen's section buttons."""

    def _root(self, lang="ru", **cfg_overrides):
        return chat_panel_root_keyboard(
            lang,
            chat_id=CHAT_ID,
            row=None,
            config=_cfg(**cfg_overrides),
            kb_status=False,
            reactions_status=(False, True),
        )

    def _group_text(self, kb, group: FieldGroup) -> str:
        btn = _row_for(kb, f"adm_pnl_grp:ru:{CHAT_ID}:{group.value}")
        assert btn is not None
        return btn.text

    def test_toggle_group_counts_only_enabled(self):
        """Flipping one toggle off must move the count by exactly one."""
        before = self._group_text(self._root(rag_enabled=True), FieldGroup.MODULES)
        after = self._group_text(self._root(rag_enabled=False), FieldGroup.MODULES)
        assert before != after
        on_before = int(before.rsplit("вкл ", 1)[1].split("/")[0])
        on_after = int(after.rsplit("вкл ", 1)[1].split("/")[0])
        assert on_before - on_after == 1

    def test_toggle_group_total_counts_bools_only(self):
        """Stickers holds 3 toggles + 4 numeric fields — the total is 3, not 7."""
        text = self._group_text(self._root(), FieldGroup.STICKERS)
        assert text.endswith("/3")

    def test_group_without_toggles_reports_its_size(self):
        """Behavior has no BOOL fields, so 'on N/0' would be nonsense."""
        text = self._group_text(self._root(), FieldGroup.BEHAVIOR)
        assert "вкл" not in text
        assert "5 настроек" in text

    def test_russian_plural_forms(self):
        from src.bot.keyboards.admin_chat_panel import _settings_word

        assert _settings_word(1, "ru") == "настройка"
        assert _settings_word(3, "ru") == "настройки"
        assert _settings_word(5, "ru") == "настроек"
        assert _settings_word(11, "ru") == "настроек"  # 11 is not "одиннадцать настройка"
        assert _settings_word(21, "ru") == "настройка"
        assert _settings_word(1, "en") == "setting"
        assert _settings_word(2, "en") == "settings"


class TestInheritedMarkerRoot:
    """B-2: "inherited from default" suffix on the KB/Reactions link rows."""

    def test_kb_link_row_marked_when_raw_is_null(self):
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row={"kb_enabled": None},
            config=_cfg(),
            kb_status=True,
            reactions_status=(False, True),
        )
        btn = _row_for(kb, f"adm_kb_menu:ru:{CHAT_ID}")
        assert btn is not None
        assert "унаследовано" in btn.text

    def test_kb_link_row_not_marked_when_raw_is_explicit(self):
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row={"kb_enabled": True},
            config=_cfg(),
            kb_status=True,
            reactions_status=(False, True),
        )
        btn = _row_for(kb, f"adm_kb_menu:ru:{CHAT_ID}")
        assert btn is not None
        assert "унаследовано" not in btn.text

    def test_reactions_link_row_marks_each_half_independently(self):
        # reactions_enabled explicitly set, reactions_history_enabled
        # inherited -- the marker must attach to only its own half.
        kb = chat_panel_root_keyboard(
            "ru",
            chat_id=CHAT_ID,
            row={"reactions_enabled": True, "reactions_history_enabled": None},
            config=_cfg(),
            kb_status=False,
            reactions_status=(True, False),
        )
        btn = _row_for(kb, f"adm_react_menu:ru:{CHAT_ID}")
        assert btn is not None
        assert btn.text.count("унаследовано") == 1
        # The marked half is the history status, which comes after " / ".
        before, _, after = btn.text.partition(" / ")
        assert "унаследовано" not in before
        assert "унаследовано" in after


class TestInheritedMarkerGroup:
    """B-2: "inherited from default" suffix, only for ``not legacy`` rows."""

    def _config(self, **overrides) -> ChatConfig:
        return replace(ChatConfig(chat_id=CHAT_ID), **overrides)

    def test_new_field_marked_when_raw_is_null(self):
        # link_comments_enabled ("lc") is legacy=False, group MODULES.
        config = self._config(link_comments_enabled=True)
        kb = chat_panel_group_keyboard(
            "ru",
            chat_id=CHAT_ID,
            group=FieldGroup.MODULES,
            config=config,
            row={"link_comments_enabled": None},
        )
        btn = _row_for(kb, f"adm_pnl_tgl:ru:{CHAT_ID}:lc")
        assert btn is not None
        assert "унаследовано" in btn.text

    def test_new_field_not_marked_when_raw_is_explicit(self):
        config = self._config(link_comments_enabled=True)
        kb = chat_panel_group_keyboard(
            "ru",
            chat_id=CHAT_ID,
            group=FieldGroup.MODULES,
            config=config,
            row={"link_comments_enabled": True},
        )
        btn = _row_for(kb, f"adm_pnl_tgl:ru:{CHAT_ID}:lc")
        assert btn is not None
        assert "унаследовано" not in btn.text

    def test_new_field_not_marked_when_row_missing_entirely(self):
        # No chat_settings row at all is still "not explicitly set" -- but
        # exercised separately from row=None to document the row-is-None
        # default used by every other test in this module means "inherited".
        config = self._config(link_comments_enabled=False)
        kb = chat_panel_group_keyboard(
            "ru", chat_id=CHAT_ID, group=FieldGroup.MODULES, config=config, row=None
        )
        btn = _row_for(kb, f"adm_pnl_tgl:ru:{CHAT_ID}:lc")
        assert btn is not None
        assert "унаследовано" in btn.text

    def test_legacy_field_never_marked_even_if_raw_is_null(self):
        # rag_enabled ("rag") is legacy=True, group MODULES -- must never
        # show the marker, even in the (shouldn't-happen) case its column
        # reads NULL.
        config = self._config(rag_enabled=True)
        kb = chat_panel_group_keyboard(
            "ru",
            chat_id=CHAT_ID,
            group=FieldGroup.MODULES,
            config=config,
            row={"rag_enabled": None},
        )
        btn = _row_for(kb, f"adm_pnl_tgl:ru:{CHAT_ID}:rag")
        assert btn is not None
        assert "унаследовано" not in btn.text

    def test_non_bool_new_field_marked(self):
        # rules_mode ("rm") is legacy=False, non-BOOL, group RULES.
        config = self._config(rules_mode="strict")
        kb = chat_panel_group_keyboard(
            "ru",
            chat_id=CHAT_ID,
            group=FieldGroup.RULES,
            config=config,
            row={"rules_mode": None},
        )
        labels = _get_labels(kb)
        assert any("strict" in label and "унаследовано" in label for label in labels)
