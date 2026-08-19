"""Tests for src.bot.settings_fields — the per-chat settings field registry.

Cross-checks the registry against the two other sources of truth it must stay
in sync with:
- ``ChatConfigService._CHAT_CONFIG_FIELDS`` (which fields are mergeable at all)
- ``ChatSettingsRepository._WRITABLE_COLUMNS`` (which fields can be written)
so registry drift (someone adds a field to ChatConfig but forgets the
registry, or vice versa) fails loudly instead of silently under-rendering the
panel/defaults screen.
"""

from __future__ import annotations

import re

from src.bot.settings_fields import (
    CHAT_SETTINGS_FIELDS,
    FIELDS_BY_CODE,
    FIELDS_BY_KEY,
    FieldGroup,
    FieldType,
    bool_fields,
    field_by_code,
    field_by_key,
    fields_by_group,
    group_label,
    new_fields,
)
from src.database.repositories.chat_settings import _WRITABLE_COLUMNS
from src.services.chat_config import _CHAT_CONFIG_FIELDS

# The 13 migration-001 columns that still carry a SQL DEFAULT (PRD "Критичное
# легаси" / CLAUDE.md "Per-chat columns: nullable, no DEFAULT"). Spelled out
# explicitly so a future edit to FieldSpec.legacy that silently drops or adds
# a field fails this test instead of shipping a defaults screen that lies.
_EXPECTED_LEGACY_KEYS = frozenset(
    {
        "trigger_words",
        "random_response_chance",
        "random_response_min_interval",
        "system_prompt",
        "language",
        "rag_enabled",
        "transcribe_voice",
        "transcribe_video_notes",
        "abuse_filter_enabled",
        "sticker_learning_enabled",
        "sticker_response_chance",
        "image_analysis_enabled",
        "save_messages",
    }
)

_CODE_RE = re.compile(r"^[a-z0-9]{1,4}$")


# Mergeable fields deliberately absent from the settings panel: `enabled` is
# the whitelist (managed from the admin DM, not the panel); `is_forum` is chat
# metadata the middleware copies from Telegram's Chat object (TD-102) — a
# toggle for it would invite overriding what only Telegram decides, and the
# next event would silently write the truth back anyway.
_NON_PANEL_FIELDS = frozenset({"enabled", "is_forum"})


class TestRegistryCoverage:
    """The registry must track ChatConfig's mergeable fields exactly."""

    def test_covers_all_mergeable_fields_except_non_panel(self):
        registry_keys = set(FIELDS_BY_KEY)
        assert registry_keys | _NON_PANEL_FIELDS == _CHAT_CONFIG_FIELDS

    def test_non_panel_fields_are_excluded(self):
        assert not _NON_PANEL_FIELDS & set(FIELDS_BY_KEY)

    def test_kb_organizer_ids_is_excluded(self):
        assert "kb_organizer_ids" not in FIELDS_BY_KEY

    def test_all_registry_keys_are_writable_columns(self):
        assert set(FIELDS_BY_KEY) <= _WRITABLE_COLUMNS

    def test_field_count_is_25(self):
        assert len(CHAT_SETTINGS_FIELDS) == 25

    def test_no_duplicate_keys(self):
        keys = [f.key for f in CHAT_SETTINGS_FIELDS]
        assert len(keys) == len(set(keys))


class TestLegacySplit:
    """Legacy (SQL DEFAULT) vs. new (nullable, no DEFAULT) — gates B-2 and C-1."""

    def test_legacy_keys_match_migration_001(self):
        legacy_keys = {f.key for f in CHAT_SETTINGS_FIELDS if f.legacy}
        assert legacy_keys == _EXPECTED_LEGACY_KEYS

    def test_legacy_count_is_13(self):
        assert sum(1 for f in CHAT_SETTINGS_FIELDS if f.legacy) == 13

    def test_new_fields_count_is_12(self):
        assert len(new_fields()) == 12

    def test_new_fields_are_exactly_the_non_legacy_fields(self):
        # FieldSpec.label is a dict (unhashable) so compare by key, not identity-in-a-set.
        expected_keys = {f.key for f in CHAT_SETTINGS_FIELDS if not f.legacy}
        assert {f.key for f in new_fields()} == expected_keys

    def test_new_fields_never_include_a_legacy_field(self):
        assert all(not f.legacy for f in new_fields())


class TestCallbackCodes:
    """Short codes exist because full field names don't fit callback_data."""

    def test_codes_are_unique(self):
        codes = [f.code for f in CHAT_SETTINGS_FIELDS]
        assert len(codes) == len(set(codes))

    def test_codes_are_short_lowercase_alnum(self):
        for f in CHAT_SETTINGS_FIELDS:
            assert _CODE_RE.match(f.code), f"{f.key!r} code {f.code!r} fails format/length"

    def test_field_by_code_roundtrips(self):
        for f in CHAT_SETTINGS_FIELDS:
            assert field_by_code(f.code) is f

    def test_field_by_code_unknown_returns_none(self):
        assert field_by_code("nope") is None

    def test_field_by_key_roundtrips(self):
        for f in CHAT_SETTINGS_FIELDS:
            assert field_by_key(f.key) is f

    def test_field_by_key_unknown_returns_none(self):
        assert field_by_key("nope") is None

    def test_fields_by_code_matches_by_key_count(self):
        assert len(FIELDS_BY_CODE) == len(FIELDS_BY_KEY) == len(CHAT_SETTINGS_FIELDS)


class TestGrouping:
    """fields_by_group() must cover every field exactly once, order preserved."""

    def test_every_field_appears_exactly_once(self):
        seen: list[str] = []
        for _group, fields in fields_by_group():
            seen.extend(f.key for f in fields)
        assert sorted(seen) == sorted(f.key for f in CHAT_SETTINGS_FIELDS)

    def test_group_order_matches_first_occurrence_in_declaration(self):
        expected_order = []
        for f in CHAT_SETTINGS_FIELDS:
            if f.group not in expected_order:
                expected_order.append(f.group)
        actual_order = [group for group, _fields in fields_by_group()]
        assert actual_order == expected_order

    def test_all_field_group_values_have_labels(self):
        for group in FieldGroup:
            assert group_label(group, "ru")
            assert group_label(group, "en")

    def test_group_label_falls_back_to_ru_for_unknown_lang(self):
        assert group_label(FieldGroup.KB, "fr") == group_label(FieldGroup.KB, "ru")


class TestFieldTypes:
    """Only BOOL fields are v1-toggleable; the split must match the domain model."""

    def test_bool_fields_are_all_type_bool(self):
        assert all(f.type is FieldType.BOOL for f in bool_fields())

    def test_bool_field_count(self):
        # 25 total, 10 non-bool (trigger_words, random_response_chance,
        # random_response_min_interval, system_prompt, language,
        # sticker_response_chance, sticker_reply_to_sticker_chance,
        # image_comment_sticker_chance, tolerance_level, rules_mode) => 15 bool.
        assert len(bool_fields()) == 15

    def test_every_field_has_a_valid_type(self):
        assert all(isinstance(f.type, FieldType) for f in CHAT_SETTINGS_FIELDS)


class TestLabels:
    """Every field must have both ru and en labels (i18n dict convention)."""

    def test_every_field_has_ru_and_en_labels(self):
        for f in CHAT_SETTINGS_FIELDS:
            assert f.label.get("ru"), f.key
            assert f.label.get("en"), f.key

    def test_label_for_returns_requested_lang(self):
        field = field_by_key("kb_enabled")
        assert field is not None
        assert field.label_for("en") == field.label["en"]
        assert field.label_for("ru") == field.label["ru"]

    def test_label_for_falls_back_to_ru_for_unknown_lang(self):
        field = field_by_key("kb_enabled")
        assert field is not None
        assert field.label_for("fr") == field.label["ru"]
