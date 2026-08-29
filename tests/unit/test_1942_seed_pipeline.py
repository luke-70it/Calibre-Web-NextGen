# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""M2 production seeding for owned Kobo annotation authority (#1942)."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, g, make_response
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.readingservices as rs
from cps.services import kobo_annotation_authority as authority
from cps.services import kobo_annotation_seeding as seeding


OWNED = "053742ff-9094-43b2-8511-c0763c90ffab"
BOOK_ID = 540
USER_ID = 107
DEVICE_A = 2
DEVICE_B = 3


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    yield database
    database.close()
    engine.dispose()


@pytest.fixture
def app(monkeypatch):
    application = Flask(__name__)
    user = SimpleNamespace(
        id=USER_ID,
        name="reader",
        is_authenticated=True,
        kobo_two_way_annotation_sync=True,
    )
    monkeypatch.setattr(rs, "current_user", user)
    monkeypatch.setattr(
        rs.config, "config_kobo_two_way_annotation_sync", True, raising=False,
    )
    monkeypatch.setattr(
        "cps.services.kobo_annotation_stage0.schema_capable",
        lambda _engine: True,
    )
    monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_a, **_k: None)
    authority.reset_skip_log_for_testing()
    return application


def _book(monkeypatch):
    book = SimpleNamespace(
        id=BOOK_ID,
        uuid="{053742FF-9094-43B2-8511-C0763C90FFAB}",
        title="Probe book",
        identifiers=[],
    )
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _value: book)
    return book


def _device(session, device_id, *, active=True):
    row = ub.Device(
        id=device_id,
        public_id=f"00000000-0000-0000-0000-{device_id:012d}",
        user_id=USER_ID,
        kind="kobo",
        display_name=f"Kobo {device_id}",
        active=active,
        created_by="auto",
    )
    session.add(row)
    return row


def _state(session, *, status="unseeded", ever=False, content_id=None):
    row = ub.KoboAnnotationBookState(
        user_id=USER_ID,
        book_id=BOOK_ID,
        content_id=content_id or f"legacy-book:{BOOK_ID}",
        authority_status=status,
        authority_revision=0,
        ever_authoritative=ever,
        generation_id="00000000-0000-0000-0000-000000000001",
        opaque_content_status="unknown",
    )
    session.add(row)
    return row


def _wire_annotation(annotation_id):
    return {
        "id": annotation_id,
        "type": "highlight",
        "highlightedText": f"text {annotation_id}",
        "highlightColor": "#F6F3B3",
        "clientLastModifiedUtc": "2026-08-29T01:00:00.000Z",
        "location": {"span": {
            "chapterFilename": "OEBPS/chapter.xhtml",
            "chapterProgress": 0.25,
            "startPath": "p/1",
            "endPath": "p/1",
            "startChar": 1,
            "endChar": 8,
        }},
        "attachments": {},
    }


def _upstream_response(annotations, *, next_offset=None, etag='W/"kobo-seed"'):
    body = json.dumps(
        {"annotations": annotations, "nextPageOffsetToken": next_offset},
        separators=(",", ":"),
    ).encode()
    return make_response(body, 200, {"Content-Type": "application/json", "ETag": etag})


def _request(app, content_id=OWNED, *, offset=None, headers=None):
    query = "?limit=100"
    if offset is not None:
        query += f"&pageOffsetToken={offset}"
    return app.test_request_context(
        f"/api/v3/content/{content_id}/annotations{query}",
        method="GET",
        headers=headers or {},
    )


def test_capture_page_persists_reconciles_and_repairs_legacy_content_id(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session, content_id=f"legacy-book:{BOOK_ID}")
    session.add(ub.Annotation(
        user_id=USER_ID,
        book_id=BOOK_ID,
        annotation_id="seeded-1",
        source="kobo",
        origin_device_id=None,
        content_revision=4,
        client_modified_at=datetime(2026, 8, 28, 1, 0, 0),
        hidden=False,
    ))
    session.commit()
    upstream = _upstream_response([_wire_annotation("seeded-1")])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response is upstream
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    page = session.query(ub.KoboAnnotationSeedCapturePage).one()
    state = session.query(ub.KoboAnnotationBookState).one()
    annotation = session.query(ub.Annotation).one()
    materialization = session.query(ub.KoboAnnotationMaterialization).one()
    assert capture.result == "accepted"
    assert capture.seed_kind == "upstream_capture"
    assert capture.annotation_count == 1
    assert capture.page_count == 1
    assert gzip.decompress(page.response_body_gzip) == upstream.get_data()
    assert page.response_sha256 == hashlib.sha256(upstream.get_data()).hexdigest()
    assert page.response_etag == 'W/"kobo-seed"'
    assert page.request_offset_token is None
    assert page.next_offset_token is None
    assert state.content_id == OWNED
    assert state.authority_status == "authoritative"
    assert state.authority_revision == 1
    assert state.ever_authoritative is True
    assert state.seeded_at is not None
    assert annotation.origin_device_id == DEVICE_A
    assert annotation.content_revision == 5
    assert materialization.provenance == "kobo_cloud_seed"
    assert materialization.serveable is True


def test_refuses_promotion_when_visible_count_is_below_capture(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.commit()
    upstream = _upstream_response([
        _wire_annotation("seeded-a"), _wire_annotation("seeded-b"),
    ])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)
    monkeypatch.setattr(seeding, "_visible_count", lambda *_a: 1)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert state.authority_status == "quarantined"
    assert state.ever_authoritative is False
    assert state.quarantine_reason == "seed_local_count_below_capture"
    assert capture.result == "rejected"
    assert capture.failure_reason == "seed_local_count_below_capture"
    assert capture.annotation_count == 2


def test_refuses_promotion_when_visible_set_exceeds_100(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.add_all([
        ub.Annotation(
            user_id=USER_ID,
            book_id=BOOK_ID,
            annotation_id=f"local-{index:03d}",
            source="webreader",
            hidden=False,
        )
        for index in range(101)
    ])
    session.commit()
    upstream = _upstream_response([])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: upstream)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert state.authority_status == "quarantined"
    assert state.quarantine_reason == "seed_local_set_requires_pagination"
    assert capture.result == "rejected"
    assert capture.annotation_count == 0


def test_persists_every_page_but_quarantines_multi_page_capture(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _state(session)
    session.commit()
    responses = iter([
        _upstream_response([_wire_annotation("page-1")], next_offset="cursor-2"),
        _upstream_response([_wire_annotation("page-2")]),
    ])
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda **_k: next(responses))

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)
    with _request(app, offset="cursor-2"):
        g.annotation_origin_device_id = DEVICE_A
        second = rs.handle_annotations.__wrapped__(OWNED)

    assert first.status_code == second.status_code == 200
    pages = session.query(ub.KoboAnnotationSeedCapturePage).order_by(
        ub.KoboAnnotationSeedCapturePage.page_number,
    ).all()
    assert [(p.page_number, p.request_offset_token, p.next_offset_token) for p in pages] == [
        (0, None, "cursor-2"), (1, "cursor-2", None),
    ]
    state = session.query(ub.KoboAnnotationBookState).one()
    capture = session.query(ub.KoboAnnotationSeedCapture).one()
    assert state.authority_status == "quarantined"
    assert state.quarantine_reason == "seed_capture_requires_pagination"
    assert capture.page_count == 2
    assert capture.annotation_count == 2


def test_final_page_processing_is_idempotent(app, session, monkeypatch):
    book = _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session)
    session.commit()
    response = _upstream_response([_wire_annotation("once")])

    with _request(app):
        capture_id = seeding.begin_or_resume_capture(
            settings=rs.config,
            user=rs.current_user,
            book=book,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        )
        assert seeding.record_proxy_response(
            capture_id,
            response=response,
            book=book,
            user=rs.current_user,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        ) is True
        assert seeding.record_proxy_response(
            capture_id,
            response=response,
            book=book,
            user=rs.current_user,
            device_id=DEVICE_A,
            request_offset_token=None,
            log=rs.log,
        ) is False

    session.refresh(state)
    assert state.authority_revision == 1
    assert session.query(ub.KoboAnnotationSeedCapture).count() == 1
    assert session.query(ub.KoboAnnotationSeedCapturePage).count() == 1
    assert session.query(ub.Annotation).count() == 1


def test_ever_authoritative_gate_failure_still_acks_patch_byte_exact(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _state(session, status="quarantined", ever=True, content_id=OWNED)
    session.commit()
    monkeypatch.setattr(rs, "_stage_patch_for_recovery", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("sticky local authority resumed Kobo forwarding"),
    )
    monkeypatch.setattr(
        rs.config, "config_kobo_two_way_annotation_sync", False, raising=False,
    )

    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH", json={},
    ):
        g.annotation_origin_device_id = 999
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 204
    assert response.get_data() == b""
    assert response.headers["Content-Type"] == "text/html"
    assert response.headers["Content-Length"] == "0"


def test_local_get_ignores_if_none_match_and_never_returns_304(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    state = _state(session, status="authoritative", ever=True, content_id=OWNED)
    state.authority_revision = 1
    session.flush()
    session.add(ub.KoboAnnotationSeedCapture(
        book_state_id=state.id,
        device_id=DEVICE_A,
        completed_at=datetime.now(timezone.utc),
        annotation_count=0,
        page_count=1,
        result="accepted",
        seed_kind="upstream_capture",
    ))
    session.commit()
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("eligible local GET unexpectedly proxied"),
    )

    with _request(app, headers={"If-None-Match": "*"}):
        g.annotation_origin_device_id = DEVICE_A
        response = rs.handle_annotations.__wrapped__(OWNED)

    assert response.status_code == 200
    assert response.status_code != 304
    assert json.loads(response.get_data()) == {
        "annotations": [], "nextPageOffsetToken": None,
    }
    assert response.headers["ETag"].startswith('W/"CWNG:')


def test_mixed_active_devices_seed_on_their_next_get(
    app, session, monkeypatch,
):
    _book(monkeypatch)
    _device(session, DEVICE_A)
    _device(session, DEVICE_B)
    _state(session)
    session.commit()
    upstream = _upstream_response([_wire_annotation("shared")])
    proxy_devices = []

    def proxy(**_kwargs):
        proxy_devices.append(g.annotation_origin_device_id)
        return upstream

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", proxy)

    with _request(app):
        g.annotation_origin_device_id = DEVICE_A
        first = rs.handle_annotations.__wrapped__(OWNED)

    state = session.query(ub.KoboAnnotationBookState).one()
    partial = seeding.seed_coverage(user_id=USER_ID, book_state_id=state.id)
    assert first.status_code == 200
    assert partial == {
        "active_device_count": 2,
        "accepted_device_count": 1,
        "missing_device_count": 1,
        "consistently_local": False,
        "books_partially_seeded": 1,
    }

    with _request(app):
        g.annotation_origin_device_id = DEVICE_B
        second = rs.handle_annotations.__wrapped__(OWNED)

    complete = seeding.seed_coverage(user_id=USER_ID, book_state_id=state.id)
    assert second.status_code == 200
    assert proxy_devices == [DEVICE_A, DEVICE_B]
    assert complete == {
        "active_device_count": 2,
        "accepted_device_count": 2,
        "missing_device_count": 0,
        "consistently_local": True,
        "books_partially_seeded": 0,
    }
    assert session.query(ub.KoboAnnotationSeedCapture).count() == 2
    annotation = session.query(ub.Annotation).one()
    assert annotation.origin_device_id == DEVICE_A


def test_additive_migration_is_idempotent_and_backfills_safety_history(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pre-m2.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE kobo_annotation_book_state (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL, content_id TEXT NOT NULL,
                authority_status TEXT NOT NULL, authority_revision INTEGER NOT NULL,
                generation_id TEXT, opaque_content_status TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE kobo_annotation_seed_capture (
                id INTEGER PRIMARY KEY, book_state_id INTEGER NOT NULL,
                device_id INTEGER, started_at DATETIME, completed_at DATETIME,
                annotation_count INTEGER, page_count INTEGER, result TEXT NOT NULL,
                failure_reason TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE kobo_annotation_seed_capture_page (
                id INTEGER PRIMARY KEY, seed_capture_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL, response_body_gzip BLOB NOT NULL,
                response_sha256 TEXT NOT NULL
            )
        """))
        conn.execute(text(
            "INSERT INTO kobo_annotation_book_state VALUES "
            "(1,107,540,'legacy-book:540','authoritative',1,'generation','absent')"
        ))
        conn.execute(text(
            "INSERT INTO kobo_annotation_seed_capture "
            "(id,book_state_id,annotation_count,page_count,result) "
            "VALUES (1,1,0,1,'accepted')"
        ))

    ub.migrate_kobo_annotation_seed_pipeline(engine, None)
    ub.migrate_kobo_annotation_seed_pipeline(engine, None)

    columns = {
        table: [column["name"] for column in inspect(engine).get_columns(table)]
        for table in (
            "kobo_annotation_book_state", "kobo_annotation_seed_capture",
        )
    }
    assert columns["kobo_annotation_book_state"].count("ever_authoritative") == 1
    assert columns["kobo_annotation_seed_capture"].count("seed_kind") == 1
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT ever_authoritative FROM kobo_annotation_book_state WHERE id=1"
        )).scalar_one() == 1
        assert conn.execute(text(
            "SELECT seed_kind FROM kobo_annotation_seed_capture WHERE id=1"
        )).scalar_one() == "routing_only"
    engine.dispose()
