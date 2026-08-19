# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The re-delivery measurement must be unable to touch an annotated book.

scripts/measure_kobo_redelivery.py answers the one question blocking F-3e383a and
notes/3649: does a Kobo re-download when the server sends a ChangedEntitlement?

It runs against the operator's own device and mutates `Books.last_modified`, so
its safety properties are worth a test rather than a comment:

* it REFUSES a book that carries any annotation, because if the answer turns out
  to be "yes it re-delivers", a re-spined package could strand exactly those;
* it does nothing at all without `--go`;
* it never deletes a kobo_synced_books row — that empties the user's tracking
  table, resets the whole sync token, and would answer a different question.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "measure_kobo_redelivery.py"


def _module():
    spec = importlib.util.spec_from_file_location("measure_kobo_redelivery", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _app_db(tmp_path, annotations, name="app.db"):
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE annotation (id INTEGER PRIMARY KEY, book_id INTEGER)")
    conn.executemany("INSERT INTO annotation (book_id) VALUES (?)",
                     [(7,)] * annotations)
    conn.commit()
    conn.close()
    return path


def _metadata_db(tmp_path):
    path = tmp_path / "metadata.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, last_modified TEXT)")
    conn.execute("INSERT INTO books (id, last_modified) VALUES (7, '2020-01-01')")
    conn.commit()
    conn.close()
    return path


def test_the_script_exists_and_loads():
    assert SCRIPT.is_file(), SCRIPT
    assert callable(_module().main)


def test_it_refuses_a_book_that_has_annotations(tmp_path, capsys):
    module = _module()
    app_db = _app_db(tmp_path, annotations=3)
    metadata_db = _metadata_db(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        module.main([
            "--book-id", "7", "--device-path", "/mnt/onboard/x.kepub.epub",
            "--app-db", str(app_db), "--metadata-db", str(metadata_db), "--go",
        ])
    assert "REFUSING" in str(excinfo.value)

    # And it refused BEFORE changing anything.
    conn = sqlite3.connect(metadata_db)
    try:
        assert conn.execute(
            "SELECT last_modified FROM books WHERE id = 7").fetchone()[0] == "2020-01-01"
    finally:
        conn.close()


def test_a_dry_run_changes_nothing_even_for_a_clean_book(tmp_path):
    module = _module()
    app_db = _app_db(tmp_path, annotations=0)
    metadata_db = _metadata_db(tmp_path)

    assert module.main([
        "--book-id", "7", "--device-path", "/mnt/onboard/x.kepub.epub",
        "--app-db", str(app_db), "--metadata-db", str(metadata_db),
    ]) == 0

    conn = sqlite3.connect(metadata_db)
    try:
        assert conn.execute(
            "SELECT last_modified FROM books WHERE id = 7").fetchone()[0] == "2020-01-01"
    finally:
        conn.close()


def test_the_clean_book_case_really_is_clean(tmp_path):
    """Vacuity guard: the refusal test would also pass if EVERY book refused."""
    module = _module()
    assert module.annotations_for_book(
        _app_db(tmp_path, annotations=0, name="clean.db"), 7) == 0
    assert module.annotations_for_book(
        _app_db(tmp_path, annotations=3, name="dirty.db"), 7) == 3


def test_the_script_issues_no_delete_at_all():
    """Deleting the user's last kobo_synced_books row empties the tracking table,
    which resets the whole sync token and turns every book into a
    NewEntitlement — answering a different question entirely.

    This is a source check on purpose, and it is the legitimate kind: it proves
    an ABSENCE in a script whose dangerous path cannot be executed here without
    a device. It does not claim a runtime behaviour it never runs.
    """
    import re

    source = SCRIPT.read_text(encoding="utf-8")
    # Strip the module docstring, which explains at length what it does NOT do.
    body = source.split('"""', 2)[-1]
    assert not re.search(r"\bDELETE\s+FROM\b", body, re.I), (
        "the script contains a DELETE; it must only ever bump last_modified"
    )


def test_the_only_write_is_a_last_modified_bump():
    """One UPDATE, one column, one book."""
    import re

    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]
    writes = [" ".join(m.split()).upper()
              for m in re.findall(r"(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\s+\w+",
                                  body, re.I)]
    assert writes == ["UPDATE BOOKS"], writes
