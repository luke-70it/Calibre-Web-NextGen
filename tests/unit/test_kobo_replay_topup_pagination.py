# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Replay suppression must be transparent to Kobo page capacity."""

from datetime import datetime, timedelta

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import sync_harness


pytestmark = pytest.mark.unit


def _wire_entitlements(response):
    return [
        (kind, payload["BookEntitlement"])
        for item in response.get_json()
        for kind, payload in item.items()
        if kind in {"NewEntitlement", "ChangedEntitlement"}
    ]


def _populate_library(sync_harness, count):
    """Create a stable id-ordered candidate set of exactly ``count`` books."""
    from cps import db

    base = datetime(2026, 1, 1)
    sync_harness.book.timestamp = base
    sync_harness.book.last_modified = base
    books = [sync_harness.book]
    for offset in range(1, count):
        modified = base + timedelta(seconds=offset)
        book = db.Books(
            f"Top-up Book {offset + 1}",
            f"Top-up Book {offset + 1}",
            "Author",
            modified,
            db.Books.DEFAULT_PUBDATE,
            "1.0",
            modified,
            f"top-up-book-{offset + 1}",
            0,
            [],
            [],
        )
        sync_harness.session.add(book)
        sync_harness.session.flush()
        book.uuid = f"00000000-0000-0000-0002-{book.id:012d}"
        sync_harness.session.add(db.Data(
            book.id,
            "EPUB",
            3_000_000 + offset,
            f"top-up-book-{offset + 1}",
        ))
        books.append(book)
    sync_harness.session.commit()
    return books


def _acknowledge_all_live_books(sync_harness, books):
    """Walk returned cursors until every live entitlement is acknowledged."""
    from cps import kobo, ub

    token = None
    responses = []
    max_pages = (
        len(books) + kobo.SYNC_ITEM_LIMIT - 1
    ) // kobo.SYNC_ITEM_LIMIT
    for _page in range(max_pages):
        response = sync_harness.sync(token)
        responses.append(response)
        token = response.headers[sync_harness.token_header]
    assert sync_harness.session.query(
        ub.KoboDeviceBookEntitlement,
    ).filter_by(device_id=sync_harness.device.id).count() == len(books)
    return responses


def test_exact_page_prefix_does_not_hide_changed_book_on_first_response(
    sync_harness, monkeypatch,
):
    """One changed book behind 100 exact rows is delivered immediately."""
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(sync_harness, kobo.SYNC_ITEM_LIMIT + 1)
    _acknowledge_all_live_books(sync_harness, books)

    changed = books[-1]
    changed.last_modified = datetime(2027, 1, 1)
    sync_harness.session.commit()

    response = sync_harness.sync(None, acknowledge=False)
    entitlements = _wire_entitlements(response)

    assert len(entitlements) == 1
    assert entitlements[0][0] == "ChangedEntitlement"
    assert entitlements[0][1]["Id"] == str(changed.uuid)


def test_exact_page_prefix_does_not_hide_missing_ledger_book_on_first_response(
    sync_harness, monkeypatch,
):
    """One never-received row behind 100 exact rows remains New immediately."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(sync_harness, kobo.SYNC_ITEM_LIMIT + 1)
    _acknowledge_all_live_books(sync_harness, books)

    missing = books[-1]
    sync_harness.session.query(ub.KoboDeviceBookEntitlement).filter_by(
        device_id=sync_harness.device.id,
        book_id=missing.id,
    ).delete(synchronize_session=False)
    sync_harness.session.commit()

    response = sync_harness.sync(None, acknowledge=False)
    entitlements = _wire_entitlements(response)

    assert len(entitlements) == 1
    assert entitlements[0][0] == "NewEntitlement"
    assert entitlements[0][1]["Id"] == str(missing.uuid)


def test_exact_tombstone_prefix_does_not_hide_new_tombstone_on_first_response(
    sync_harness, monkeypatch,
):
    """One new deletion behind 100 acknowledged tombstones emits immediately."""
    from cps import kobo, ub

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    deleted_base = datetime(2026, 1, 1)
    for offset in range(kobo.SYNC_ITEM_LIMIT):
        sync_harness.session.add(ub.KoboDeletedBook(
            user_id=sync_harness.user.id,
            book_uuid=f"00000000-0000-0000-0003-{offset:012d}",
            deleted_at=deleted_base + timedelta(seconds=offset),
        ))
    sync_harness.session.commit()

    initial = sync_harness.sync()
    assert sum(
        entitlement["IsRemoved"] is True
        for _kind, entitlement in _wire_entitlements(initial)
    ) == kobo.SYNC_ITEM_LIMIT

    new_uuid = "00000000-0000-0000-0003-000000000100"
    sync_harness.session.add(ub.KoboDeletedBook(
        user_id=sync_harness.user.id,
        book_uuid=new_uuid,
        deleted_at=deleted_base + timedelta(seconds=kobo.SYNC_ITEM_LIMIT),
    ))
    sync_harness.session.commit()

    response = sync_harness.sync(None, acknowledge=False)
    removed = [
        entitlement for _kind, entitlement in _wire_entitlements(response)
        if entitlement["IsRemoved"] is True
    ]

    assert len(removed) == 1
    assert removed[0]["Id"] == new_uuid


def test_fully_suppressed_250_book_scan_is_bounded_and_terminal(
    sync_harness, monkeypatch,
):
    """A finite exact-only snapshot drains without continuation or over-scan."""
    from cps import kobo

    monkeypatch.setattr(
        kobo.config, "config_kobo_suppress_replayed_entitlements", True,
    )
    books = _populate_library(sync_harness, 250)
    _acknowledge_all_live_books(sync_harness, books)

    original_fingerprint = kobo._entitlement_fingerprint
    rendered = []

    def counted_fingerprint(entitlement):
        rendered.append(entitlement["BookEntitlement"]["Id"])
        return original_fingerprint(entitlement)

    monkeypatch.setattr(kobo, "_entitlement_fingerprint", counted_fingerprint)
    response = sync_harness.sync(None, acknowledge=False)

    assert response.get_json() == []
    assert response.headers.get("x-kobo-sync") is None
    assert len(rendered) == len(set(rendered)) == 250
