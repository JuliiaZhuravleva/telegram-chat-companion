"""Write every registered sample screen to one JSON file, for the screen-map renderer.

    python3 scripts/dump_screens.py                      # → /tmp/screens-dump.json, ru labels
    python3 scripts/dump_screens.py --out x.json --lang en

Offline: no bot, no database, no network. The samples are built from the
invented values in ``src/bot/screens/samples/fake.py``, and a button's target
is read off its callback (`routes.target_of`) — the same walk the rules test
does. Every rule a sample breaks is printed as a warning on stderr, so a dump
taken mid-work shows what is left; the exit code stays 0, because the dump is
a picture, not a gate (``tests/unit/test_screen_rules.py`` is the gate).

Output shape::

    {"screens": [{"id", "section", "title", "text_ru", "text_en",
                  "surface": "dm" | "group", "kind": "msg",
                  "rows": [[{"label", "goes_to"}]]}],
     "kept": {}}

``goes_to`` is a screen id, or a description where a button opens no screen:
``"ссылка"`` for a URL, ``"копирует текст"`` for a copy button, ``"нет действия"``
for an inert counter. Reply-keyboard rows follow the inline ones, as
``"выбор в Telegram"`` (a ``request_users`` picker) or ``"ввод текстом"``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# The handler tree is imported to resolve where callbacks land, and importing it
# builds `Settings`, which insists on these two. Neither is used: nothing here
# talks to Telegram or the database. A real value already in the environment wins.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "offline-dump-screens")
os.environ.setdefault("DATABASE_URL", "postgresql://offline@localhost:1/none")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from src.bot.screens.i18n import Lang
    from src.bot.screens.model import Screen
    from src.bot.screens.registry import Sample

DEFAULT_OUT = "/tmp/screens-dump.json"  # noqa: S108 — the renderer's agreed default path
URL_TARGET = "ссылка"
COPY_TARGET = "копирует текст"
PICK_TARGET = "выбор в Telegram"
TYPED_TARGET = "ввод текстом"
LANGS: tuple[Lang, ...] = ("ru", "en")


def _rows(screen: Screen) -> list[list[dict[str, str]]]:
    from src.bot.screens.routes import target_of

    rows: list[list[dict[str, str]]] = []
    for row in screen.rows:
        out: list[dict[str, str]] = []
        for button in row:
            if button.goes_to is not None:
                goes_to = button.goes_to
            elif button.url is not None:
                goes_to = URL_TARGET
            elif button.copy_text is not None:
                goes_to = COPY_TARGET
            else:
                goes_to = target_of(button.callback or "", screen.id)
            out.append({"label": button.label, "goes_to": goes_to})
        rows.append(out)
    for reply_row in screen.reply or ():
        rows.append(
            [
                {
                    "label": button.label,
                    "goes_to": PICK_TARGET if button.request_users is not None else TYPED_TARGET,
                }
                for button in reply_row
            ]
        )
    return rows


def _item(sample: Sample, lang: Lang) -> dict[str, Any]:
    from src.bot.screens.render import render_text

    screen = sample.build(lang)
    return {
        "id": sample.screen_id,
        "section": sample.section,
        "title": sample.title,
        "text_ru": render_text(sample.build("ru")),
        "text_en": render_text(sample.build("en")),
        "surface": "group" if sample.nav == "group" else "dm",
        "kind": "msg",
        "rows": _rows(screen),
    }


def dump(lang: Lang) -> tuple[dict[str, Any], list[str]]:
    """The dump document and the lint warnings, without writing anything."""
    from src.bot.handlers import router as main_router
    from src.bot.screens.lint import check_screen, first_handler
    from src.bot.screens.registry import load_all

    def route(data: str) -> str | None:
        return first_handler(main_router, data)

    screens: list[dict[str, Any]] = []
    warnings: list[str] = []
    for screen_id, variants in sorted(load_all().items()):
        for sample in variants:
            screens.append(_item(sample, lang))
            for check_lang in LANGS:
                built = sample.build(check_lang)
                for problem in check_screen(built, sample, check_lang, route=route):
                    warnings.append(f"{screen_id} / {sample.title} [{check_lang}]: {problem}")
    return {"screens": screens, "kept": {}}, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"output path (default {DEFAULT_OUT})")
    parser.add_argument("--lang", default="ru", choices=("ru", "en"), help="button label language")
    args = parser.parse_args(argv)

    from src.bot.screens.i18n import lang_of

    document, warnings = dump(lang_of(args.lang))
    out = Path(args.out)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"{len(document['screens'])} screens → {out} ({len(warnings)} rule warnings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
