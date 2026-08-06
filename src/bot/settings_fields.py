"""Registry of per-chat settings fields — shared by the chat settings panel
(B-1) and the "settings by default" screen (C-1).

Single source of truth for how each of the 25 per-chat settings fields (a
subset of ``ChatConfig`` / ``ChatConfigService._CHAT_CONFIG_FIELDS``, see
``src/services/chat_config.py``) is grouped, labeled, typed, and addressed in
inline-keyboard ``callback_data``. Both consumer screens must render the same
grouping/labels/legacy split, so it lives here once rather than drifting
between two handler modules.

Excluded from this registry (25 = 26 mergeable ``ChatConfig`` fields, minus
these two):

- ``enabled`` — the whitelist gate itself, managed by the approve/reject/
  remove flow (``adm_approve:`` / ``adm_wl_rm:``), not a "setting" a chat or
  the defaults screen can toggle (there is no ``default_enabled`` key —
  migration 001 never seeds one).
- ``kb_organizer_ids`` — JSONB list, stays in the KB panel
  (``adm_kb_orgs:``) per the A-2 ADR; not part of ``ChatConfig`` either (it's
  written straight through ``ChatSettingsRepository``, never merged).

Legacy vs. new (``FieldSpec.legacy``): 13 of these 25 columns still carry a
SQL ``DEFAULT`` from migration 001 (``alembic/versions/001_initial_schema.py``),
so ``ensure_exists()`` materializes a per-chat value on first contact and
permanently shadows ``bot_config.default_*`` for that field on every chat the
bot has already seen — the "inherited from default" story is false for them
until the forward-only migration in C-2 (deferred to tech debt). The other 12
are nullable, no-DEFAULT columns (CLAUDE.md "Per-chat columns: nullable, no
DEFAULT") where NULL honestly means "inherited". Consumers:

- B-2 (inheritance marker on the chat panel) must only show "inherited" for
  ``not legacy`` fields.
- C-1 (defaults screen) must only expose ``not legacy`` fields — the defaults
  screen would otherwise lie for the 13 legacy ones.

``FieldType`` only distinguishes BOOL (v1: toggle-able) from everything else
(v1: read-only display — FSM editing of STR/FLOAT/INT/STR_LIST fields is F-1,
deferred to a later iteration).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FieldGroup(StrEnum):
    """Sections a per-chat field belongs to. Declaration order == render order."""

    BEHAVIOR = "behavior"
    MODULES = "modules"
    STICKERS = "stickers"
    RULES = "rules"
    KB = "kb"
    REACTIONS = "reactions"


class FieldType(StrEnum):
    """Value type of a settings field.

    Only BOOL fields get a v1 toggle button; the rest render read-only until
    F-1 (deferred) adds FSM-driven editing.
    """

    BOOL = "bool"
    STR = "str"
    FLOAT = "float"
    INT = "int"
    STR_LIST = "str_list"


_GROUP_LABELS: dict[FieldGroup, dict[str, str]] = {
    FieldGroup.BEHAVIOR: {"ru": "💬 Поведение", "en": "💬 Behavior"},
    FieldGroup.MODULES: {"ru": "🧩 Модули", "en": "🧩 Modules"},
    FieldGroup.STICKERS: {"ru": "🎨 Стикеры", "en": "🎨 Stickers"},
    FieldGroup.RULES: {"ru": "📏 Правила", "en": "📏 Rules"},
    FieldGroup.KB: {"ru": "📚 База знаний", "en": "📚 Knowledge Base"},
    FieldGroup.REACTIONS: {"ru": "😀 Реакции", "en": "😀 Reactions"},
}


@dataclass(frozen=True)
class FieldSpec:
    """One per-chat settings field, as shown on the chat panel / defaults screen."""

    key: str
    """``ChatConfig`` / ``chat_settings`` column name."""

    group: FieldGroup

    label: dict[str, str]
    """i18n label, ``{"ru": ..., "en": ...}``."""

    code: str
    """Short, unique ``callback_data`` token. Telegram's 64-byte callback_data
    limit plus a numeric ``chat_id`` (up to 14 chars incl. sign) leaves little
    room for a field name verbatim — ``sticker_reply_to_sticker_enabled`` is
    33 chars alone. Kept lowercase alnum, <= 4 chars (enforced by test)."""

    type: FieldType

    legacy: bool
    """True for the 13 migration-001 columns that still carry a SQL DEFAULT
    (see module docstring). False for the 11 new nullable/no-DEFAULT columns."""

    def label_for(self, lang: str) -> str:
        """Resolve the i18n label, falling back to ru then the raw key."""
        return self.label.get(lang, self.label.get("ru", self.key))


def group_label(group: FieldGroup, lang: str) -> str:
    """Resolve a section header label, falling back to ru then the raw group value."""
    labels = _GROUP_LABELS.get(group, {})
    return labels.get(lang, labels.get("ru", group.value))


# Declaration order == render order within each group (mirrors the PRD's
# per-chat fields table, docs/plans/chat-settings-panel-2026-08-06.md).
CHAT_SETTINGS_FIELDS: tuple[FieldSpec, ...] = (
    # ── Behavior — all legacy, all non-bool (F-1, deferred, owns editing) ──
    FieldSpec(
        "trigger_words",
        FieldGroup.BEHAVIOR,
        {"ru": "Триггер-слова", "en": "Trigger words"},
        "tw",
        FieldType.STR_LIST,
        legacy=True,
    ),
    FieldSpec(
        "random_response_chance",
        FieldGroup.BEHAVIOR,
        {"ru": "Вероятность случайного ответа", "en": "Random response chance"},
        "rc",
        FieldType.FLOAT,
        legacy=True,
    ),
    FieldSpec(
        "random_response_min_interval",
        FieldGroup.BEHAVIOR,
        {"ru": "Мин. интервал случайных ответов", "en": "Random response min interval"},
        "ri",
        FieldType.INT,
        legacy=True,
    ),
    FieldSpec(
        "system_prompt",
        FieldGroup.BEHAVIOR,
        {"ru": "Системный промпт", "en": "System prompt"},
        "sp",
        FieldType.STR,
        legacy=True,
    ),
    FieldSpec(
        "language",
        FieldGroup.BEHAVIOR,
        {"ru": "Язык ответов", "en": "Response language"},
        "lg",
        FieldType.STR,
        legacy=True,
    ),
    # ── Modules ──────────────────────────────────────────────────────────
    FieldSpec(
        "rag_enabled",
        FieldGroup.MODULES,
        {"ru": "RAG-память", "en": "RAG memory"},
        "rag",
        FieldType.BOOL,
        legacy=True,
    ),
    FieldSpec(
        "transcribe_voice",
        FieldGroup.MODULES,
        {"ru": "Распознавание голоса", "en": "Voice transcription"},
        "tv",
        FieldType.BOOL,
        legacy=True,
    ),
    FieldSpec(
        "transcribe_video_notes",
        FieldGroup.MODULES,
        {"ru": "Распознавание видеосообщений", "en": "Video note transcription"},
        "tn",
        FieldType.BOOL,
        legacy=True,
    ),
    FieldSpec(
        "abuse_filter_enabled",
        FieldGroup.MODULES,
        {"ru": "Antiabuse-фильтр", "en": "Abuse filter"},
        "af",
        FieldType.BOOL,
        legacy=True,
    ),
    FieldSpec(
        "save_messages",
        FieldGroup.MODULES,
        {"ru": "Сохранять историю сообщений", "en": "Save message history"},
        "sm",
        FieldType.BOOL,
        legacy=True,
    ),
    FieldSpec(
        "image_analysis_enabled",
        FieldGroup.MODULES,
        {"ru": "Анализ изображений", "en": "Image analysis"},
        "ia",
        FieldType.BOOL,
        legacy=True,
    ),
    FieldSpec(
        "link_comments_enabled",
        FieldGroup.MODULES,
        {"ru": "Комментарии к ссылкам", "en": "Link comments"},
        "lc",
        FieldType.BOOL,
        legacy=False,
    ),
    FieldSpec(
        "relevancy_gate_enabled",
        FieldGroup.MODULES,
        {"ru": "Фильтр релевантности", "en": "Relevancy gate"},
        "rg",
        FieldType.BOOL,
        legacy=False,
    ),
    # ── Stickers ─────────────────────────────────────────────────────────
    FieldSpec(
        "sticker_learning_enabled",
        FieldGroup.STICKERS,
        {"ru": "Обучение на стикерах", "en": "Sticker learning"},
        "sl",
        FieldType.BOOL,
        legacy=True,
    ),
    FieldSpec(
        "sticker_response_chance",
        FieldGroup.STICKERS,
        {"ru": "Вероятность ответа стикером", "en": "Sticker response chance"},
        "sc",
        FieldType.FLOAT,
        legacy=True,
    ),
    FieldSpec(
        "sticker_reply_to_sticker_enabled",
        FieldGroup.STICKERS,
        {"ru": "Ответ стикером на стикер", "en": "Reply to sticker with a sticker"},
        "sr",
        FieldType.BOOL,
        legacy=False,
    ),
    FieldSpec(
        "sticker_reply_to_sticker_chance",
        FieldGroup.STICKERS,
        {"ru": "Вероятность ответа стикером на стикер", "en": "Reply-to-sticker chance"},
        "sx",
        FieldType.FLOAT,
        legacy=False,
    ),
    FieldSpec(
        "image_comment_sticker_enabled",
        FieldGroup.STICKERS,
        {"ru": "Стикер-комментарий к фото", "en": "Sticker comment on images"},
        "ic",
        FieldType.BOOL,
        legacy=False,
    ),
    FieldSpec(
        "image_comment_sticker_chance",
        FieldGroup.STICKERS,
        {"ru": "Вероятность стикер-комментария", "en": "Image comment sticker chance"},
        "ix",
        FieldType.FLOAT,
        legacy=False,
    ),
    FieldSpec(
        "tolerance_level",
        FieldGroup.STICKERS,
        {"ru": "Уровень приличия стикеров", "en": "Sticker tolerance level"},
        "tol",
        FieldType.FLOAT,
        legacy=False,
    ),
    # ── Rules ────────────────────────────────────────────────────────────
    FieldSpec(
        "rules_enabled",
        FieldGroup.RULES,
        {"ru": "Кастомные правила", "en": "Custom rules"},
        "re",
        FieldType.BOOL,
        legacy=False,
    ),
    FieldSpec(
        "rules_mode",
        FieldGroup.RULES,
        {"ru": "Режим правил", "en": "Rules mode"},
        "rm",
        FieldType.STR,
        legacy=False,
    ),
    # ── Knowledge base (kb_organizer_ids stays in the KB panel, see A-2) ───
    FieldSpec(
        "kb_enabled",
        FieldGroup.KB,
        {"ru": "База знаний", "en": "Knowledge base"},
        "kb",
        FieldType.BOOL,
        legacy=False,
    ),
    # ── Reactions ────────────────────────────────────────────────────────
    FieldSpec(
        "reactions_enabled",
        FieldGroup.REACTIONS,
        {"ru": "Реакции", "en": "Reactions"},
        "rx",
        FieldType.BOOL,
        legacy=False,
    ),
    FieldSpec(
        "reactions_history_enabled",
        FieldGroup.REACTIONS,
        {"ru": "История реакций", "en": "Reaction history log"},
        "rh",
        FieldType.BOOL,
        legacy=False,
    ),
)

FIELDS_BY_KEY: dict[str, FieldSpec] = {field.key: field for field in CHAT_SETTINGS_FIELDS}
FIELDS_BY_CODE: dict[str, FieldSpec] = {field.code: field for field in CHAT_SETTINGS_FIELDS}


def field_by_key(key: str) -> FieldSpec | None:
    """Look up a field spec by its ``ChatConfig`` column name."""
    return FIELDS_BY_KEY.get(key)


def field_by_code(code: str) -> FieldSpec | None:
    """Look up a field spec by its short ``callback_data`` code."""
    return FIELDS_BY_CODE.get(code)


def fields_by_group() -> tuple[tuple[FieldGroup, tuple[FieldSpec, ...]], ...]:
    """Group fields for rendering, preserving declaration order on both axes."""
    groups: dict[FieldGroup, list[FieldSpec]] = {}
    for field in CHAT_SETTINGS_FIELDS:
        groups.setdefault(field.group, []).append(field)
    return tuple((group, tuple(fields)) for group, fields in groups.items())


def new_fields() -> tuple[FieldSpec, ...]:
    """The 12 fields eligible for the defaults screen (C-1) and the
    "inherited from default" marker (B-2) — the 13 legacy columns lie about
    inheritance until migration C-2 lands."""
    return tuple(field for field in CHAT_SETTINGS_FIELDS if not field.legacy)


def bool_fields() -> tuple[FieldSpec, ...]:
    """Fields that get a v1 toggle button (the rest render read-only)."""
    return tuple(field for field in CHAT_SETTINGS_FIELDS if field.type is FieldType.BOOL)
