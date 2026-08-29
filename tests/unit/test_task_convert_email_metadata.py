# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Producer-chain coverage for conversion-triggered e-reader email tasks."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    "explicit_title, discovered_title, expected_title",
    [
        ("Queued conversion title", "Database title", "Queued conversion title"),
        (None, "Database title", "Database title"),
    ],
)
def test_convert_forwards_book_metadata_to_internal_email(
        monkeypatch, explicit_title, discovered_title, expected_title):
    # Production reaches TaskConvert through cps.helper; that import order also
    # resolves convert.py's longstanding helper/convert module cycle.
    from cps import helper
    from cps.tasks import convert
    from cps.tasks.mail import TaskEmail

    monkeypatch.setattr(convert.config, "config_use_google_drive", False, raising=False)
    task = helper.TaskConvert(
        "/library/Author/Book/book",
        1851,
        "EPUB -> MOBI",
        {"subject": "Converted book", "body": "Attached book"},
        "reader@example.invalid",
        user="alice",
        book_title=explicit_title,
    )

    def complete_conversion():
        task.title = discovered_title
        task.results["path"] = "Author/Book"
        return "book.mobi"

    monkeypatch.setattr(task, "_convert_ebook_format", complete_conversion)
    worker = MagicMock()

    task.run(worker)

    worker.add.assert_called_once()
    queued_user, queued_task = worker.add.call_args.args
    assert queued_user == "alice"
    assert isinstance(queued_task, TaskEmail)
    assert queued_task.book_id == 1851
    assert queued_task.book_title == expected_title
