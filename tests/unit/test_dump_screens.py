"""``scripts/dump_screens.py`` produces what the screen-map renderer reads.

The renderer indexes every item with ``[...]``, so one missing key is a
``KeyError`` that stops the whole map, not one screen drawn badly. The keys
pinned here are the ones it reads that way.
"""

from __future__ import annotations

import json

import pytest

from scripts.dump_screens import dump

SCREEN_KEYS = {
    "id",
    "section",
    "title",
    "text_ru",
    "text_en",
    "surface",
    "kind",
    "rows",
    "replaces",
}


@pytest.fixture(scope="module")
def document() -> dict:
    doc, _warnings = dump("ru")
    return doc


def test_the_document_has_screens_and_kept(document: dict):
    assert isinstance(document["kept"], dict)
    assert document["screens"], "no registered sample was dumped"


def test_every_screen_has_every_key_the_renderer_reads(document: dict):
    for item in document["screens"]:
        assert set(item) >= SCREEN_KEYS, f"{item.get('id')}: missing {SCREEN_KEYS - set(item)}"
        assert isinstance(item["replaces"], list)
        assert item["kind"] == "msg"
        for row in item["rows"]:
            for button in row:
                assert {"label", "goes_to"} <= set(button), f"{item['id']}: {button}"


def test_the_stale_sample_is_dumped_and_serialises(document: dict):
    ids = [item["id"] for item in document["screens"]]
    assert "stale" in ids
    json.dumps(document, ensure_ascii=False)
