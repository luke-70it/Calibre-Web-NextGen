# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline builder tests for the never-merge ZZWB payload."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "zzwb_build_annotation_envelope.py"
SPEC = importlib.util.spec_from_file_location("zzwb_build_annotation_envelope", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _raw(annotation_id, location, *, text):
    return (
        b'{ "id" : "' + annotation_id.encode("ascii")
        + b'", "type":"highlight", "clientLastModifiedUtc":"2026-08-18T00:00:00Z",'
        + b' "highlightedText":"' + text + b'", "location" : ' + location
        + b', "attachments":{} }'
    )


@pytest.fixture
def stage0_db(tmp_path):
    path = tmp_path / "app snapshot.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE annotation (
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL,
          book_id INTEGER NOT NULL,
          annotation_id TEXT NOT NULL,
          hidden BOOLEAN
        );
        CREATE TABLE kobo_annotation_book_state (
          user_id INTEGER NOT NULL,
          book_id INTEGER NOT NULL,
          content_id TEXT NOT NULL
        );
        CREATE TABLE kobo_annotation_materialization (
          annotation_id INTEGER NOT NULL,
          raw_annotation_json BLOB NOT NULL,
          raw_location_json BLOB NOT NULL,
          payload_sha256 TEXT NOT NULL,
          provenance TEXT NOT NULL
        );
    """)
    connection.execute(
        "INSERT INTO kobo_annotation_book_state VALUES (?, ?, ?)",
        (7, builder.PROBE_BOOK_ID, builder.PROBE_CONTENT_ID),
    )
    location_b = b'{ "span" : {"chapterFilename":"b.xhtml","startPath":"p\\/b"} }'
    location_a = b'{"span":{"chapterFilename":"a.xhtml","startPath":"p\\/a"}}'
    raw_b = _raw("ann-b", location_b, text=b"second")
    raw_a = _raw("ann-a", location_a, text=b"first\\u0020exact")
    raw_hidden = _raw("ann-hidden", location_a, text=b"must-not-appear")
    for row_id, annotation_id, hidden, raw, location in (
        (1, "ann-b", 0, raw_b, location_b),
        (2, "ann-a", 0, raw_a, location_a),
        (3, "ann-hidden", 1, raw_hidden, location_a),
    ):
        connection.execute(
            "INSERT INTO annotation VALUES (?, ?, ?, ?, ?)",
            (row_id, 7, builder.PROBE_BOOK_ID, annotation_id, hidden),
        )
        connection.execute(
            "INSERT INTO kobo_annotation_materialization VALUES (?, ?, ?, ?, ?)",
            (row_id, raw, location, hashlib.sha256(raw).hexdigest(), "kobo_patch"),
        )
    connection.commit()
    connection.close()
    return path, raw_a, raw_b, location_a, location_b


def test_builder_emits_measured_envelope_with_exact_objects_and_locations(stage0_db):
    path, raw_a, raw_b, location_a, location_b = stage0_db
    database_before = path.read_bytes()

    result = builder.build_envelope(path)

    expected = (
        b'{"annotations":[' + raw_a + b"," + raw_b
        + b'],"nextPageOffsetToken":null}'
    )
    assert result.payload == expected
    assert result.annotation_count == 2
    assert result.user_id == 7
    assert result.book_id == 540
    assert result.sha256 == hashlib.sha256(expected).hexdigest()
    assert location_a in result.payload
    assert location_b in result.payload
    assert b"must-not-appear" not in result.payload
    assert path.read_bytes() == database_before


def test_builder_cli_writes_payload_from_read_only_snapshot(stage0_db, tmp_path):
    path, raw_a, raw_b, _location_a, _location_b = stage0_db
    output = tmp_path / "payload.json"

    assert builder.main([
        "--database", str(path), "--output", str(output),
    ]) == 0

    assert output.read_bytes() == (
        b'{"annotations":[' + raw_a + b"," + raw_b
        + b'],"nextPageOffsetToken":null}'
    )


@pytest.mark.parametrize("damage", ["sha", "location", "annotation_id", "provenance"])
def test_builder_refuses_materialization_invariant_damage(stage0_db, damage):
    path, _raw_a, _raw_b, _location_a, _location_b = stage0_db
    connection = sqlite3.connect(path)
    if damage == "sha":
        connection.execute(
            "UPDATE kobo_annotation_materialization SET payload_sha256='0' WHERE annotation_id=2"
        )
    elif damage == "location":
        connection.execute(
            "UPDATE kobo_annotation_materialization SET raw_location_json='{}' "
            "WHERE annotation_id=2"
        )
    elif damage == "annotation_id":
        connection.execute("UPDATE annotation SET annotation_id='different' WHERE id=2")
    else:
        connection.execute(
            "UPDATE kobo_annotation_materialization SET provenance='cwng_authored' "
            "WHERE annotation_id=2"
        )
    connection.commit()
    connection.close()

    with pytest.raises(builder.BuildError):
        builder.build_envelope(path)


def test_builder_refuses_empty_probe_by_default_and_requires_explicit_override(tmp_path):
    path = tmp_path / "empty.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE annotation (
          id INTEGER PRIMARY KEY, user_id INTEGER, book_id INTEGER,
          annotation_id TEXT, hidden BOOLEAN
        );
        CREATE TABLE kobo_annotation_book_state (
          user_id INTEGER, book_id INTEGER, content_id TEXT
        );
        CREATE TABLE kobo_annotation_materialization (
          annotation_id INTEGER, raw_annotation_json BLOB, raw_location_json BLOB,
          payload_sha256 TEXT, provenance TEXT
        );
    """)
    connection.execute(
        "INSERT INTO kobo_annotation_book_state VALUES (7, 540, ?)",
        (builder.PROBE_CONTENT_ID,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(builder.BuildError, match="zero visible materializations"):
        builder.build_envelope(path)
    result = builder.build_envelope(path, allow_empty=True)
    assert result.payload == b'{"annotations":[],"nextPageOffsetToken":null}'


def test_builder_cannot_select_a_different_content_id(stage0_db):
    path, _raw_a, _raw_b, _location_a, _location_b = stage0_db
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE kobo_annotation_book_state SET content_id='different-book'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        builder.BuildError, match="^offline snapshot has no probe book state$",
    ):
        builder.build_envelope(path)


def test_builder_refuses_probe_uuid_mapped_to_any_other_calibre_book(stage0_db):
    path, _raw_a, _raw_b, _location_a, _location_b = stage0_db
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE kobo_annotation_book_state SET book_id=541"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        builder.BuildError, match="^probe UUID resolves to book 541, expected 540$",
    ):
        builder.build_envelope(path)


def _server_highlight():
    return {
        "attachments": {},
        "clientLastModifiedUtc": "2026-08-28T01:23:45.000Z",
        "context": "A hardware-only sentence around the selected words.",
        "highlightColor": "#A0A0A0",
        "highlightedText": "selected words",
        "id": "8ec42a5c-9c83-4c5f-a83f-5abca03e281d",
        "location": {
            "span": {
                "chapterFilename": "OEBPS/chapter-01.xhtml",
                "chapterProgress": 0.25,
                "endChar": 14,
                "endPath": "/span[@id='kobo.1.1']/text()",
                "startChar": 0,
                "startPath": "/span[@id='kobo.1.1']/text()",
            },
        },
        "type": "highlight",
    }


def test_builder_emits_one_server_authored_highlight_in_exact_kobo_get_shape(tmp_path):
    specification = tmp_path / "server-highlight.json"
    specification.write_text(json.dumps(_server_highlight()), encoding="utf-8")

    result = builder.build_server_highlight_envelope(specification)

    expected_object = json.dumps(
        _server_highlight(), ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    assert result.payload == (
        b'{"annotations":[' + expected_object
        + b'],"nextPageOffsetToken":null}'
    )
    assert result.annotation_count == 1
    assert result.user_id is None
    assert result.book_id is None
    assert result.sha256 == hashlib.sha256(result.payload).hexdigest()
    parsed = json.loads(result.payload)
    assert list(parsed) == ["annotations", "nextPageOffsetToken"]
    assert parsed["annotations"] == [_server_highlight()]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda item: item.pop("context"), "fields differ from captured GET shape"),
        (lambda item: item.update({"noteText": "not a pure highlight"}),
         "fields differ from captured GET shape"),
        (lambda item: item.update({"attachments": {"opaque": True}}),
         "attachments must be the observed empty object"),
        (lambda item: item.update({"highlightColor": "red"}),
         "outside Kobo's measured five-color palette"),
        (lambda item: item.update({"id": "not-a-uuid"}),
         "id must be a UUID"),
        (lambda item: item.update({"clientLastModifiedUtc": "2026-08-28"}),
         "must be UTC RFC 3339 ending Z"),
        (lambda item: item["location"]["span"].pop("endPath"),
         "span differs from captured GET shape"),
    ],
)
def test_builder_refuses_server_highlight_shape_drift(tmp_path, mutation, message):
    highlight = _server_highlight()
    mutation(highlight)
    specification = tmp_path / "invalid-server-highlight.json"
    specification.write_text(json.dumps(highlight), encoding="utf-8")

    with pytest.raises(builder.BuildError, match=message):
        builder.build_server_highlight_envelope(specification)


def test_builder_cli_server_highlight_mode_writes_exactly_one_row(tmp_path):
    specification = tmp_path / "server-highlight.json"
    specification.write_text(json.dumps(_server_highlight()), encoding="utf-8")
    output = tmp_path / "payload.json"

    assert builder.main([
        "--server-highlight", str(specification),
        "--output", str(output),
    ]) == 0

    assert json.loads(output.read_bytes()) == {
        "annotations": [_server_highlight()],
        "nextPageOffsetToken": None,
    }
