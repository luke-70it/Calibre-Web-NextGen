# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for the Kobo archive/deletion cursor."""

from datetime import datetime, timedelta

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import sync_harness


pytestmark = pytest.mark.unit


def _outgoing_token(sync_harness, response):
    from cps.services import SyncToken

    return SyncToken.SyncToken.from_headers({
        sync_harness.token_header:
            response.headers[sync_harness.token_header],
    })


def _removed_book_ids(response):
    return [
        item["ChangedEntitlement"]["BookEntitlement"]["Id"]
        for item in response.get_json()
        if item.get("ChangedEntitlement", {}).get(
            "BookEntitlement", {},
        ).get("IsRemoved") is True
    ]


def test_empty_archive_pass_never_regresses_consumed_tombstone_cursor(
    sync_harness, monkeypatch,
):
    """Consumed tombstones cannot make archive_modified alternate with epoch."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    # Cross the migration boundary before these deletions exist so their first
    # delivery is real and later requests exercise acknowledged suppression.
    initial = sync_harness.sync()
    incoming_header = initial.headers[sync_harness.token_header]
    assert sync_harness.session.query(ub.ArchivedBook).count() == 0

    deleted_at = datetime(2026, 8, 28, 13, 0, 0)
    deleted_ids = {
        f"00000000-0000-0000-0004-{sequence:012d}"
        for sequence in range(5)
    }
    sync_harness.session.add_all([
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid=book_uuid,
            deleted_at=deleted_at + timedelta(seconds=sequence),
        )
        for sequence, book_uuid in enumerate(sorted(deleted_ids))
    ])
    sync_harness.session.commit()

    removed_by_request = []
    archive_cursors = []
    for _request in range(6):
        incoming = kobo.SyncToken.SyncToken.from_headers({
            sync_harness.token_header: incoming_header,
        })
        response = sync_harness.sync(incoming_header)
        outgoing = _outgoing_token(sync_harness, response)

        assert outgoing.archive_last_modified >= \
            incoming.archive_last_modified
        removed_by_request.append(set(_removed_book_ids(response)))
        archive_cursors.append(outgoing.archive_last_modified)
        incoming_header = response.headers[sync_harness.token_header]

    assert removed_by_request[0] == deleted_ids
    assert removed_by_request[1:] == [set()] * 5
    assert archive_cursors == [archive_cursors[0]] * 6


def test_newer_archive_change_does_not_mask_unseen_tombstone(
    sync_harness, monkeypatch,
):
    """Tombstone selection remains based on the reader's incoming cursor."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )

    initial = sync_harness.sync()
    incoming_header = initial.headers[sync_harness.token_header]
    incoming = _outgoing_token(sync_harness, initial)
    deleted_at = datetime(2026, 8, 28, 13, 0, 0)
    archived_at = deleted_at + timedelta(hours=1)
    deleted_id = "00000000-0000-0000-0004-000000000099"

    # Keep this live row inside the changed-entry snapshot. Its newer archive
    # clock must advance the response without becoming the tombstone query's
    # lower bound and masking the distinct deletion between the two cursors.
    sync_harness.book.last_modified = deleted_at - timedelta(minutes=30)

    sync_harness.session.add_all([
        ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid=deleted_id,
            deleted_at=deleted_at,
        ),
        ub.ArchivedBook(
            user_id=sync_harness.user.id,
            book_id=sync_harness.book.id,
            is_archived=True,
            last_modified=archived_at,
        ),
    ])
    sync_harness.session.commit()

    first = sync_harness.sync(incoming_header)
    first_outgoing = _outgoing_token(sync_harness, first)
    assert incoming.archive_last_modified < deleted_at < archived_at
    assert _removed_book_ids(first).count(deleted_id) == 1
    assert first_outgoing.archive_last_modified == archived_at

    second = sync_harness.sync(
        first.headers[sync_harness.token_header],
    )
    second_outgoing = _outgoing_token(sync_harness, second)
    assert deleted_id not in _removed_book_ids(second)
    assert second_outgoing.archive_last_modified == archived_at
