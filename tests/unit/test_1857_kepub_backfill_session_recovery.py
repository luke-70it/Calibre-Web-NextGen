# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for fork #1857's poisoned KEPUB backfill session."""

from types import SimpleNamespace

import pytest

from cps.services.worker import STAT_FAIL


pytestmark = pytest.mark.unit


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def distinct(self):
        return self

    def all(self):
        return list(self._rows)


def _wire_common(monkeypatch, kepub_backfill, calibre_db, book_ids):
    class AppSession:
        def query(self, *_args):
            return _Query([(book_id,) for book_id in book_ids])

        def close(self):
            pass

    saved = []
    monkeypatch.setattr(kepub_backfill.ub, "get_new_session_instance", AppSession)
    monkeypatch.setattr(kepub_backfill.db, "CalibreDB", calibre_db)
    monkeypatch.setattr(kepub_backfill, "get_epub_layout", lambda *_args: None)
    monkeypatch.setattr(
        kepub_backfill.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "config_kobo_kepub_backfill_completed", False,
        raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "get_book_path", lambda: "/books", raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "save", lambda: saved.append(True), raising=False)
    return saved


def test_closed_session_failure_rebuilds_and_processes_later_books(monkeypatch):
    """Book k poisons one Session; k+1..n run on a fresh CalibreDB/Session."""
    from cps.tasks import kepub_backfill

    instances = []
    book_queries = []

    class Session:
        def __init__(self, number):
            self.number = number
            self.is_active = True
            self.rollback_calls = 0

        def rollback(self):
            self.rollback_calls += 1
            self.is_active = True

    class CalibreDB:
        def __init__(self, **_kwargs):
            self.number = len(instances) + 1
            self.session = Session(self.number)
            instances.append(self)

        def get_book(self, book_id):
            book_queries.append((self.number, book_id))
            if self.number == 1 and book_id == 2:
                self.session.is_active = False
                raise RuntimeError(
                    "Can't reconnect until invalid transaction is rolled back")
            return SimpleNamespace(
                id=book_id, path=str(book_id), title=str(book_id))

        def get_book_format(self, _book_id, fmt):
            if fmt == "EPUB":
                return SimpleNamespace(format="EPUB", name="book")
            return None

    converted = []

    class Conversion:
        def __init__(self, _path, book_id, *_args):
            self.book_id = book_id
            self.error = None

        def _convert_ebook_format(self):
            converted.append(self.book_id)
            return "book.kepub"

    saved = _wire_common(monkeypatch, kepub_backfill, CalibreDB, [1, 2, 3, 4])
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)
    per_book_errors = []
    monkeypatch.setattr(
        kepub_backfill.log, "error_or_exception", per_book_errors.append)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert converted == [1, 3, 4]
    assert book_queries == [(1, 1), (1, 2), (2, 3), (2, 4)]
    assert len(instances) == 2
    assert instances[0].session is None
    assert task.processed == 4
    assert task.converted == 3
    assert task.skipped == 0
    assert task.failed == 1
    assert str(task.message) == "4/4 processed: 3 converted, 0 skipped, 1 failed"
    assert str(task.error) == (
        "KEPUB backfill finished with failures; "
        "4/4 processed: 3 converted, 0 skipped, 1 failed")
    assert task.stat == STAT_FAIL
    assert saved == [True]
    assert len(per_book_errors) == 1
    assert "Can't reconnect until invalid transaction is rolled back" in per_book_errors[0]


def test_session_rebuild_circuit_breaker_aborts_at_exact_threshold(monkeypatch):
    """A dead session factory produces three rebuild logs, not one per book."""
    from cps.tasks import kepub_backfill

    constructor_calls = []
    queried_books = []

    class Session:
        def __init__(self, usable):
            self.is_active = True
            self.usable = usable

        def rollback(self):
            pass

        def execute(self, _statement):
            if not self.usable:
                raise RuntimeError("session factory is unavailable")
            return SimpleNamespace(scalar=lambda: 1)

    class CalibreDB:
        def __init__(self, **_kwargs):
            constructor_calls.append(len(constructor_calls) + 1)
            self.session = Session(usable=len(constructor_calls) == 1)

        def get_book(self, book_id):
            queried_books.append(book_id)
            raise RuntimeError("Cannot operate on a closed database")

        def get_book_format(self, *_args):
            raise AssertionError("format lookup must not follow the failed book lookup")

    class Conversion:
        def __init__(self, *_args):
            raise AssertionError("conversion must not start after the database failure")

    saved = _wire_common(monkeypatch, kepub_backfill, CalibreDB, [10, 11, 12, 13])
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)
    terminal_logs = []

    def capture_error(message, *args, **_kwargs):
        terminal_logs.append(message % args if args else str(message))

    monkeypatch.setattr(kepub_backfill.log, "error", capture_error)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert constructor_calls == [1, 2, 3, 4]
    assert queried_books == [10]
    assert task.processed == 1
    assert task.converted == 0
    assert task.skipped == 0
    assert task.failed == 1
    assert str(task.message) == "1/4 processed: 0 converted, 0 skipped, 1 failed"
    assert "aborted after 3 consecutive database session rebuild failures" in str(task.error)
    assert "1/4 processed: 0 converted, 0 skipped, 1 failed" in str(task.error)
    assert task.stat == STAT_FAIL
    assert saved == []
    assert sum("aborting after 3 consecutive" in line for line in terminal_logs) == 1
    assert not any("book 11" in line or "book 12" in line or "book 13" in line
                   for line in terminal_logs)
