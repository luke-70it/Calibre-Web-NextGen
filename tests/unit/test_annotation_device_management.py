# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    from cps import ub
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def filtered_library_stub(monkeypatch):
    """Keep app.db-only fixtures explicit without weakening production authz."""
    from cps import annotations

    original = annotations._visible_book_scopes_for_owners

    def scopes(owners, _session):
        return {
            int(owner.id): frozenset(range(1, 20_001))
            for owner in owners if owner is not None
        }

    monkeypatch.setattr(annotations, "_visible_book_scopes_for_owners", scopes)
    monkeypatch.setattr(
        annotations.calibre_db,
        "get_filtered_book",
        lambda book_id, user=None: SimpleNamespace(id=book_id, title=f"Book {book_id}"),
    )
    return original


def _user(session, *, user_id=7, name=None, role=0):
    from cps import ub
    row = ub.User(
        id=user_id, name=name or f"User {user_id}", role=role,
        email=f"user-{user_id}@example.invalid",
    )
    session.add(row)
    session.flush()
    return row


def _device(session, *, user_id=7, label="Reader", active=True, kind="kobo"):
    from cps import ub
    if session.query(ub.User.id).filter(ub.User.id == user_id).first() is None:
        _user(session, user_id=user_id)
    row = ub.Device(user_id=user_id, kind=kind, display_name=label, active=active,
                    created_by="auto")
    session.add(row)
    session.flush()
    return row


def _annotation(session, annotation_id, *, user_id=7, book_id=5, origin=None,
                assigned=None, annotation_type=None, hidden=False):
    from cps import ub
    row = ub.Annotation(
        user_id=user_id, book_id=book_id, annotation_id=annotation_id,
        source="kobo", origin_device_id=origin, assigned_device_id=assigned,
        annotation_type=annotation_type, hidden=hidden,
        highlighted_text=f"Text {annotation_id}",
    )
    session.add(row)
    session.flush()
    return row


def test_device_list_aggregates_zero_and_many_annotation_counts(session):
    from cps.annotations import list_annotation_devices
    empty = _device(session, label="Empty")
    busy = _device(session, label="Busy")
    for index in range(12):
        _annotation(session, f"a-{index}", assigned=busy.id)
    rows = {row["public_id"]: row for row in list_annotation_devices(user_id=7, session=session)}
    assert rows[empty.public_id]["annotation_count"] == 0
    assert rows[busy.public_id]["annotation_count"] == 12


def test_device_list_exposes_webreader_kind_without_faking_legacy_rows(session):
    from cps import ub
    from cps.annotations import list_annotation_devices

    browser = ub.Device(
        user_id=7,
        kind="webreader",
        display_name="Web reader",
        model="CWNG web reader",
        platform="epub.js",
        active=True,
        created_by="auto",
    )
    session.add(browser)
    session.commit()
    payload = list_annotation_devices(user_id=7, session=session)[0]
    assert payload["kind"] == "webreader"
    assert payload["type"] == "webreader"
    assert payload["label"] == "Web reader"


@pytest.mark.unit
def test_device_inventory_default_page_is_bounded_at_the_write_cap(session, monkeypatch):
    from datetime import datetime, timezone

    from cps import annotations, ub

    device = _device(session)
    report = ub.DeviceInventoryReport(
        device_id=device.id, item_count=5000, matched_count=0,
        observed_at=datetime.now(timezone.utc),
    )
    session.add(report)
    session.flush()
    session.add_all([
        ub.DeviceInventoryItem(
            device_id=device.id, lpath=f"Books/{index:04d}.epub", checksum=f"{index:032x}",
            book_id=index + 1, size=index, mtime=index, last_report_id=report.id,
        )
        for index in range(5000)
    ])
    session.commit()

    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/inventory"):
        response = annotations.annotation_device_inventory.__wrapped__(device.public_id)

    payload = response.get_json()
    assert len(payload["books"]) == 200
    assert payload["total"] == 5000
    assert payload["limit"] == 200
    assert payload["offset"] == 0
    assert payload["books"][0]["lpath"] == "Books/0000.epub"
    assert payload["books"][-1]["lpath"] == "Books/0199.epub"


@pytest.mark.unit
@pytest.mark.parametrize(("query", "field"), [
    ("limit=-1", "limit"),
    ("limit=0", "limit"),
    ("limit=not-a-number", "limit"),
    ("limit=201", "limit"),
    ("limit=999999999999999999999999", "limit"),
    ("offset=-1", "offset"),
    ("offset=not-a-number", "offset"),
])
def test_device_inventory_rejects_invalid_pagination(session, monkeypatch, query, field):
    from cps import annotations, ub

    device = _device(session)
    session.commit()
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/inventory?{query}"):
        response, status = annotations.annotation_device_inventory.__wrapped__(device.public_id)

    assert status == 400
    assert response.get_json()["error"] == "invalid_pagination"
    assert response.get_json()["field"] == field


@pytest.mark.unit
def test_device_inventory_applies_limit_and_offset_and_allows_past_end(session, monkeypatch):
    from datetime import datetime, timezone

    from cps import annotations, ub

    device = _device(session)
    report = ub.DeviceInventoryReport(
        device_id=device.id, item_count=5, matched_count=0,
        observed_at=datetime.now(timezone.utc),
    )
    session.add(report)
    session.flush()
    session.add_all([
        ub.DeviceInventoryItem(
            device_id=device.id, lpath=f"Books/{index}.epub", checksum=f"{index:032x}",
            book_id=index + 1, size=index, mtime=index, last_report_id=report.id,
        )
        for index in range(5)
    ])
    session.commit()
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)

    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/inventory?limit=2&offset=0"):
        first_page = annotations.annotation_device_inventory.__wrapped__(device.public_id).get_json()
    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/inventory?limit=2&offset=2"):
        middle_page = annotations.annotation_device_inventory.__wrapped__(device.public_id).get_json()
    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/inventory"
            "?limit=2&offset=999999999999999999999999"):
        past_end = annotations.annotation_device_inventory.__wrapped__(device.public_id).get_json()

    assert [book["lpath"] for book in first_page["books"]] == [
        "Books/0.epub", "Books/1.epub",
    ]
    assert [book["lpath"] for book in middle_page["books"]] == [
        "Books/2.epub", "Books/3.epub",
    ]
    assert middle_page["total"] == 5
    assert middle_page["limit"] == 2
    assert middle_page["offset"] == 2
    assert past_end["books"] == []
    assert past_end["total"] == 5


@pytest.mark.unit
def test_device_inventory_pagination_does_not_widen_device_ownership(session, monkeypatch):
    from cps import annotations, ub
    from werkzeug.exceptions import NotFound

    other_users_device = _device(session, user_id=8)
    session.commit()
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    with app.test_request_context(
            f"/api/annotations/devices/{other_users_device.public_id}/inventory"
            "?limit=200&offset=0"):
        with pytest.raises(NotFound):
            annotations.annotation_device_inventory.__wrapped__(other_users_device.public_id)


def test_device_list_exposes_the_latest_storage_snapshot(session):
    from datetime import datetime, timedelta, timezone
    from cps import ub
    from cps.annotations import list_annotation_devices
    device = _device(session)
    earlier = ub.DeviceStorageSnapshot(
        device_id=device.id, free_bytes=100, total_bytes=1000,
        observed_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    latest = ub.DeviceStorageSnapshot(
        device_id=device.id, free_bytes=800, total_bytes=2000,
        observed_at=datetime.now(timezone.utc),
    )
    session.add_all([earlier, latest])
    session.flush()

    listed = list_annotation_devices(user_id=7, session=session)[0]

    assert listed["storage_free"] == 800
    assert listed["storage_total"] == 2000
    assert listed["storage_observed"] == latest.observed_at.isoformat()


def test_user_can_request_deletion_only_for_a_named_item_on_their_device(
        session, monkeypatch):
    from cps import annotations, ub
    device = _device(session)
    other = _device(session, user_id=8, label="Other reader")
    report = ub.DeviceInventoryReport(device_id=device.id, item_count=1, matched_count=1)
    other_report = ub.DeviceInventoryReport(device_id=other.id, item_count=1, matched_count=1)
    session.add_all([report, other_report])
    session.flush()
    own_item = ub.DeviceInventoryItem(
        device_id=device.id, book_id=1, lpath="Books/Own.epub", checksum="1" * 32,
        size=10, mtime=10, last_report_id=report.id,
    )
    other_item = ub.DeviceInventoryItem(
        device_id=other.id, book_id=2, lpath="Books/Other.epub", checksum="2" * 32,
        size=20, mtime=20, last_report_id=other_report.id,
    )
    session.add_all([own_item, other_item])
    session.commit()
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", session.commit)

    with app.test_request_context(method="POST"):
        own_response, own_status = annotations.annotation_device_inventory_delete.__wrapped__(
            device.public_id, own_item.id,
        )
    with app.test_request_context(method="POST"):
        crossed = annotations.annotation_device_inventory_delete.__wrapped__(
            device.public_id, other_item.id,
        )

    assert own_status == 202
    assert own_response.get_json()["lpath"] == "Books/Own.epub"
    assert crossed[1] == 404
    assert session.query(ub.DeviceBookDeletion).count() == 1


@pytest.mark.parametrize("label", ["", "x" * 61, " leading", "trailing ", "bad\nlabel"])
def test_device_rename_rejects_out_of_range_or_unsafe_labels(session, label):
    from cps.annotations import rename_annotation_device
    device = _device(session)
    with pytest.raises(ValueError):
        rename_annotation_device(device.public_id, user_id=7, label=label,
                                 session=session, commit=session.commit)
    assert device.display_name == "Reader"


def test_initial_labels_receive_plain_dedup_suffix(session):
    from cps.services.device_registry import upsert_kobo_device
    common = {"x-kobo-devicemodel": "Kobo Libra Colour"}
    first = upsert_kobo_device(session, user_id=7,
                               headers={**common, "x-kobo-deviceid": "a" * 64}, secret_key="key")
    second = upsert_kobo_device(session, user_id=7,
                                headers={**common, "x-kobo-deviceid": "b" * 64}, secret_key="key")
    assert first.display_name == "Kobo Libra Colour"
    assert second.display_name == "Kobo Libra Colour 2"


def test_soft_delete_preserves_origin_clears_assignment_and_restore_round_trips(session):
    from cps import ub
    from cps.annotations import (device_annotation_counts, restore_annotation_device,
                                 soft_delete_annotation_device)
    device = _device(session, label="Maggie's Libra")
    annotation = _annotation(session, "a-1", origin=device.id, assigned=device.id)
    session.add(ub.AnnotationDeviceState(annotation_id=annotation.id, device_id=device.id,
                                         desired=True, delivery_status="acknowledged"))
    session.commit()
    _, preflight = device_annotation_counts(device.public_id, user_id=7, session=session)
    assert preflight == {"origin_count": 1, "assigned_count": 1}

    deleted, counts = soft_delete_annotation_device(
        device.public_id, user_id=7, session=session, commit=session.commit,
    )
    session.refresh(annotation)
    assert counts == preflight
    assert deleted.active is False
    assert deleted.display_name == "Maggie's Libra"
    assert annotation.origin_device_id == device.id
    assert annotation.assigned_device_id is None
    assert session.query(ub.Device).filter_by(id=annotation.origin_device_id).one().display_name == "Maggie's Libra"

    restored, restored_count, conflicts = restore_annotation_device(
        device.public_id, user_id=7, session=session, commit=session.commit,
    )
    session.refresh(annotation)
    assert restored.active is True
    assert restored_count == 1 and conflicts == 0
    assert annotation.origin_device_id == device.id
    assert annotation.assigned_device_id == device.id
    state = session.query(ub.AnnotationDeviceState).filter_by(
        annotation_id=annotation.id, device_id=device.id,
    ).one()
    assert state.desired is True and state.delivery_status == "acknowledged"


def test_reassignment_changes_intent_but_never_origin(session):
    from cps import ub
    from cps.annotations import reassign_annotation
    origin = _device(session, label="Origin")
    target = _device(session, label="Target")
    annotation = _annotation(session, "move-me", origin=origin.id, assigned=origin.id)
    session.add(ub.AnnotationDeviceState(annotation_id=annotation.id, device_id=origin.id,
                                         desired=True, delivery_status="acknowledged"))
    session.commit()
    reassign_annotation(
        "move-me", user_id=7, book_id=5, assigned_device_public_id=target.public_id,
        expected_routing_revision=1, session=session, commit=session.commit,
    )
    session.refresh(annotation)
    assert annotation.origin_device_id == origin.id
    assert annotation.assigned_device_id == target.id
    assert annotation.routing_revision == 2
    states = {state.device_id: state for state in session.query(ub.AnnotationDeviceState).all()}
    assert states[origin.id].desired is False
    assert states[target.id].desired is True
    assert states[target.id].delivery_status == "pending"


def test_bulk_mixed_results_return_200_and_commit_successes(session, monkeypatch):
    from cps import annotations, ub
    target = _device(session, label="Target")
    succeeds = _annotation(session, "succeeds")
    stale = _annotation(session, "stale")
    session.commit()
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", session.commit)
    payload = {
        "assigned_device_id": target.public_id,
        "items": [
            {"book_id": 5, "annotation_id": "succeeds", "expected_routing_revision": 1},
            {"book_id": 5, "annotation_id": "stale", "expected_routing_revision": 99},
            {"book_id": 5, "annotation_id": "missing", "expected_routing_revision": 1},
        ],
    }
    with app.test_request_context("/api/annotations/assignments/bulk", method="POST", json=payload):
        response, status = annotations.annotation_assignments_bulk.__wrapped__()
    assert status == 200
    assert response.get_json()["results"] == [
        {"annotation_id": "succeeds", "ok": True},
        {"annotation_id": "stale", "ok": False, "error_code": "revision_conflict"},
        {"annotation_id": "missing", "ok": False, "error_code": "not_found"},
    ]
    session.refresh(succeeds)
    session.refresh(stale)
    assert succeeds.assigned_device_id == target.id
    assert stale.assigned_device_id is None


def test_bulk_rejects_more_than_500_items(session, monkeypatch):
    from cps import annotations, ub
    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    with app.test_request_context("/api/annotations/assignments/bulk", method="POST",
                                  json={"assigned_device_id": None, "items": [{}] * 501}):
        response, status = annotations.annotation_assignments_bulk.__wrapped__()
    assert status == 400
    assert response.get_json()["max_items"] == 500


def test_bulk_reports_commit_wrapper_failure_instead_of_false_success(session):
    from cps.annotations import bulk_reassign_annotations
    target = _device(session, label="Target")
    annotation = _annotation(session, "commit-fails")
    session.commit()

    def failed_commit():
        session.rollback()
        return False

    results = bulk_reassign_annotations(
        [{"book_id": 5, "annotation_id": "commit-fails", "expected_routing_revision": 1}],
        user_id=7, assigned_device_public_id=target.public_id,
        session=session, commit=failed_commit,
    )
    assert results == [{"annotation_id": "commit-fails", "ok": False,
                        "error_code": "database_error"}]
    session.refresh(annotation)
    assert annotation.assigned_device_id is None


@pytest.mark.parametrize(
    ("route_name", "dependency_name", "method", "path", "payload"),
    [
        ("annotation_devices_list", "list_annotation_devices", "GET",
         "/api/annotations/devices", None),
        ("annotation_device_rename", "rename_annotation_device", "PATCH",
         "/api/annotations/devices/device-1", {"label": "Reader"}),
        ("annotation_device_delete_preflight", "device_annotation_counts", "GET",
         "/api/annotations/devices/device-1/delete-preflight", None),
        ("annotation_device_delete", "soft_delete_annotation_device", "DELETE",
         "/api/annotations/devices/device-1", None),
        ("annotation_device_restore", "restore_annotation_device", "POST",
         "/api/annotations/devices/device-1/restore", None),
    ],
)


def test_device_routes_return_json_500_when_database_work_fails(
        session, monkeypatch, route_name, dependency_name, method, path, payload):
    from cps import annotations, ub

    def fail(*args, **kwargs):
        if dependency_name in ("device_annotation_counts", "restore_annotation_device"):
            raise IntegrityError("device operation", {}, RuntimeError("constraint"))
        raise RuntimeError("commit failed")

    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(annotations, dependency_name, fail)
    route = getattr(annotations, route_name).__wrapped__
    with app.test_request_context(path, method=method, json=payload):
        result = route() if route_name == "annotation_devices_list" else route("device-1")
    response, status = result
    assert status == 500
    assert response.is_json
    assert response.get_json() == {"error": "database_error"}


def test_single_reassignment_runtime_error_returns_json_500(session, monkeypatch):
    from cps import annotations, ub

    app = Flask(__name__)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(annotations, "_resolve_book_or_404", lambda book_id: SimpleNamespace(id=book_id))
    monkeypatch.setattr(
        annotations, "reassign_annotation",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("flush failed")),
    )
    with app.test_request_context(
            "/annotations/5/a-1", method="PATCH",
            json={"assigned_device_id": None, "expected_routing_revision": 1}):
        response, status = annotations.annotations_edit.__wrapped__(5, "a-1")
    assert status == 500
    assert response.is_json
    assert response.get_json() == {"error": "database_error"}


def test_device_management_migration_twice_and_downgrade_are_reversible():
    from cps import ub
    from sqlalchemy import text
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE annotation (id INTEGER PRIMARY KEY, content_id TEXT)"))
        connection.execute(text("INSERT INTO annotation VALUES (1, 'opaque-history')"))
    ub.migrate_multi_device_annotation_safe_slice(engine, None)
    ub.migrate_device_management_slice(engine, None)
    ub.migrate_device_management_slice(engine, None)
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(annotation)"))}
        assert {"origin_device_id", "assigned_device_id", "routing_revision"} <= columns
        assert connection.execute(text("SELECT content_id FROM annotation WHERE id=1")).scalar() == "opaque-history"
    ub.downgrade_device_management_slice(engine)
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(annotation)"))}
        tables = {row[0] for row in connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))}
        assert not {"origin_device_id", "assigned_device_id", "routing_revision"} & columns
        assert "annotation_device_state" not in tables
        assert "device_retired_assignment" not in tables
        assert connection.execute(text("SELECT content_id FROM annotation WHERE id=1")).scalar() == "opaque-history"


def test_device_list_uses_latest_inventory_report_without_deleting_history(session):
    from datetime import datetime, timedelta, timezone
    from cps import ub
    from cps.annotations import list_annotation_devices

    device = _device(session)
    earlier = ub.DeviceInventoryReport(
        device_id=device.id, item_count=2, matched_count=1,
        observed_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    latest = ub.DeviceInventoryReport(
        device_id=device.id, item_count=1, matched_count=1,
        observed_at=datetime.now(timezone.utc),
    )
    session.add_all([earlier, latest])
    session.flush()
    session.add_all([
        ub.DeviceInventoryItem(
            device_id=device.id, lpath="Books/Still here.epub", checksum="1" * 32,
            book_id=1, size=10, mtime=10, last_report_id=latest.id,
        ),
        ub.DeviceInventoryItem(
            device_id=device.id, lpath="Books/Omitted.epub", checksum="2" * 32,
            book_id=2, size=20, mtime=20, last_report_id=earlier.id,
        ),
    ])
    session.commit()

    listed = list_annotation_devices(user_id=7, session=session)[0]

    assert listed["inventory_count"] == 1
    assert listed["inventory_observed"] == latest.observed_at.isoformat()
    assert session.query(ub.DeviceInventoryItem).count() == 2


def _allow_books(monkeypatch, annotations, *, hidden=()):
    hidden = set(hidden)
    calls = []

    def resolve(book_id, **kwargs):
        owner = kwargs.get("user")
        calls.append((book_id, getattr(owner, "id", None), kwargs))
        if book_id in hidden:
            return None
        return SimpleNamespace(id=book_id, title=f"Book {book_id}")

    monkeypatch.setattr(annotations.calibre_db, "get_filtered_book", resolve)
    monkeypatch.setattr(
        annotations,
        "_visible_book_scopes_for_owners",
        lambda owners, _session: {
            int(owner.id): frozenset(set(range(1, 20_001)) - hidden)
            for owner in owners if owner is not None
        },
    )
    return calls


@pytest.mark.unit
def test_device_annotations_default_to_origin_and_assigned_toggle_has_facets_and_maps(
        session, monkeypatch):
    from cps import annotations, ub

    _user(session)
    origin = _device(session, label="Origin")
    other = _device(session, label="Other")
    _annotation(session, "origin-highlight", origin=origin.id, assigned=other.id,
                annotation_type="highlight")
    _annotation(session, "assigned-note", origin=other.id, assigned=origin.id,
                annotation_type="note")
    _annotation(session, "hidden-dogear", origin=origin.id, assigned=origin.id,
                annotation_type="dogear", hidden=True)
    _annotation(session, "filtered-book", book_id=99, origin=origin.id,
                assigned=origin.id, annotation_type="highlight")
    _annotation(session, "legacy-null", origin=None, assigned=None,
                annotation_type="highlight")
    session.commit()
    calls = _allow_books(monkeypatch, annotations, hidden={99})
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    app = Flask(__name__)

    with app.test_request_context(
            f"/api/annotations/devices/{origin.public_id}/annotations"):
        origin_payload = annotations.annotation_device_annotations.__wrapped__(
            origin.public_id,
        ).get_json()
    with app.test_request_context(
            f"/api/annotations/devices/{origin.public_id}/annotations"
            "?assigned=true&type=note&page=1"):
        assigned_payload = annotations.annotation_device_annotations.__wrapped__(
            origin.public_id,
        ).get_json()

    assert [row["annotation_id"] for row in origin_payload["annotations"]] == [
        "origin-highlight",
    ]
    assert origin_payload["annotations"][0]["annotation_type"] == "highlight"
    assert origin_payload["annotations"][0]["book"]["title"] == "Book 5"
    assert origin_payload["role"] == "origin"
    assert set(origin_payload["devices"]) == {origin.public_id, other.public_id}
    assert [row["annotation_id"] for row in assigned_payload["annotations"]] == [
        "assigned-note",
    ]
    assert assigned_payload["role"] == "assigned"
    assert assigned_payload["type"] == "note"
    assert calls and all(user_id == 7 for _book_id, user_id, _kwargs in calls)
    assert all(set(kwargs) == {"user"} for _book_id, _user_id, kwargs in calls)


@pytest.mark.unit
def test_device_annotations_pagination_is_fixed_and_page_number_is_capped(
        session, monkeypatch):
    from cps import annotations, ub

    _user(session)
    device = _device(session)
    for index in range(51):
        _annotation(
            session, f"row-{index:02d}", origin=device.id,
            annotation_type="highlight",
        )
    session.commit()
    _allow_books(monkeypatch, annotations)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    app = Flask(__name__)

    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/annotations?page=1"):
        first = annotations.annotation_device_annotations.__wrapped__(device.public_id).get_json()
    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/annotations?page=2"):
        second = annotations.annotation_device_annotations.__wrapped__(device.public_id).get_json()

    assert len(first["annotations"]) == annotations.DEVICE_ANNOTATION_PAGE_SIZE == 50
    assert first["total"] == 51 and first["pages"] == 2
    assert len(second["annotations"]) == 1 and second["page"] == 2

    for value in ("0", "10001", "nope"):
        with app.test_request_context(
                f"/api/annotations/devices/{device.public_id}/annotations?page={value}"):
            response, status = annotations.annotation_device_annotations.__wrapped__(
                device.public_id,
            )
        assert status == 400
        assert response.get_json()["error"] == "invalid_pagination"


@pytest.mark.unit
@pytest.mark.parametrize("route_name", [
    "annotation_device_annotations",
    "annotation_device_summary",
    "annotation_device_positions",
])
def test_device_detail_routes_hide_cross_user_device_ids_with_404(
        session, monkeypatch, route_name):
    from cps import annotations, ub
    from werkzeug.exceptions import NotFound

    _user(session)
    _user(session, user_id=8)
    foreign = _device(session, user_id=8)
    session.commit()
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    app = Flask(__name__)
    with app.test_request_context(
            f"/api/annotations/devices/{foreign.public_id}/{route_name}"):
        with pytest.raises(NotFound):
            getattr(annotations, route_name).__wrapped__(foreign.public_id)


@pytest.mark.unit
def test_device_summary_counts_only_visible_origin_rows_positions_and_seed_coverage(
        session, monkeypatch):
    from datetime import datetime, timezone
    from cps import annotations, ub

    owner = _user(session)
    device = _device(session, label="First")
    second = _device(session, label="Second")
    _annotation(session, "h", book_id=5, origin=device.id, annotation_type="highlight")
    _annotation(session, "n", book_id=5, origin=device.id, annotation_type="note")
    _annotation(session, "d", book_id=6, origin=device.id, annotation_type="dogear")
    _annotation(session, "forbidden", book_id=99, origin=device.id,
                annotation_type="highlight")
    now = datetime.now(timezone.utc)
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=device.id, book_id=5, progress_percent=25,
            server_modified_at=now,
        ),
        ub.DeviceReadingPosition(
            device_id=device.id, book_id=99, progress_percent=90,
            server_modified_at=now,
        ),
    ])
    seeded = ub.KoboAnnotationBookState(
        user_id=owner.id, book_id=5, content_id="book-5",
        authority_status="authoritative",
    )
    unseeded = ub.KoboAnnotationBookState(
        user_id=owner.id, book_id=6, content_id="book-6",
        authority_status="unseeded",
    )
    session.add_all([seeded, unseeded])
    session.flush()
    session.add(ub.KoboAnnotationSeedCapture(
        book_state_id=seeded.id, device_id=device.id, result="accepted",
        completed_at=now,
    ))
    session.commit()
    _allow_books(monkeypatch, annotations, hidden={99})
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    app = Flask(__name__)
    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/summary"):
        payload = annotations.annotation_device_summary.__wrapped__(device.public_id).get_json()

    assert payload == {
        "highlights": 1,
        "notes": 1,
        "dogears": 1,
        "books_with_position": 1,
        "last_position_at": now.replace(tzinfo=None).isoformat(),
        "seeded_books": 1,
        "unseeded_books": 1,
    }
    listed = {
        row["public_id"]: row
        for row in annotations.list_annotation_devices(user_id=7, session=session)
    }
    assert listed[device.public_id]["authority"] == {
        "unseeded": 1,
        "seeding": 0,
        "authoritative": 1,
        "quarantined": 0,
        "disabled": 0,
        "books_partially_seeded": 1,
    }
    assert listed[second.public_id]["seeded_books"] == 0


@pytest.mark.unit
def test_device_positions_return_per_book_rows_from_the_owner_filtered_view(
        session, monkeypatch):
    from datetime import datetime, timezone
    from cps import annotations, ub

    _user(session)
    device = _device(session)
    now = datetime.now(timezone.utc)
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=device.id, book_id=5, location_source="cfi",
            location_type="cfi", location_value="epubcfi(/6/2)", cfi="epubcfi(/6/2)",
            progress_percent=12.5, content_source_progress_percent=10,
            client_modified_at=now, server_modified_at=now, rehydrate_needed=True,
        ),
        ub.DeviceReadingPosition(
            device_id=device.id, book_id=99, progress_percent=80,
            server_modified_at=now,
        ),
    ])
    session.commit()
    _allow_books(monkeypatch, annotations, hidden={99})
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    app = Flask(__name__)
    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/positions"):
        payload = annotations.annotation_device_positions.__wrapped__(device.public_id).get_json()

    assert payload["total"] == 1
    assert payload["positions"] == [{
        "book_id": 5,
        "book": {"id": 5, "title": "Book 5"},
        "location_source": "cfi",
        "location_type": "cfi",
        "location_value": "epubcfi(/6/2)",
        "progress_percent": 12.5,
        "content_source_progress_percent": 10.0,
        "cfi": "epubcfi(/6/2)",
        "client_modified_at": now.replace(tzinfo=None).isoformat(),
        "server_modified_at": now.replace(tzinfo=None).isoformat(),
        "rehydrate_needed": True,
    }]


@pytest.mark.unit
def test_admin_device_board_is_gated_filtered_and_never_invents_null_origin_device(
        session, monkeypatch):
    from cps import admin, annotations, ub
    from werkzeug.exceptions import Forbidden

    first_user = _user(session, user_id=7, name="First")
    second_user = _user(session, user_id=8, name="Second")
    first = _device(session, user_id=7, label="Browser", kind="webreader")
    second = _device(session, user_id=8, label="Kobo")
    _annotation(session, "first", user_id=7, origin=first.id,
                annotation_type="highlight")
    _annotation(session, "second", user_id=8, origin=second.id,
                annotation_type="note")
    _annotation(session, "null-origin", user_id=7, origin=None,
                annotation_type="dogear")
    session.commit()
    calls = _allow_books(monkeypatch, annotations)
    monkeypatch.setattr(ub, "session", session)
    app = Flask(__name__)

    monkeypatch.setattr(admin, "current_user", SimpleNamespace(role_admin=lambda: False))
    with app.test_request_context("/api/admin/devices"):
        with pytest.raises(Forbidden):
            annotations.annotation_admin_devices()

    monkeypatch.setattr(admin, "current_user", SimpleNamespace(role_admin=lambda: True))
    with app.test_request_context("/api/admin/devices"):
        payload = annotations.annotation_admin_devices().get_json()

    assert [(row["user"]["name"], row["label"]) for row in payload["devices"]] == [
        (first_user.name, "Browser"),
        (second_user.name, "Kobo"),
    ]
    board = {row["public_id"]: row for row in payload["devices"]}
    assert board[first.public_id]["highlights"] == 1
    assert board[first.public_id]["dogears"] == 0
    assert board[first.public_id]["kind_label"] == "Web reader"
    assert board[second.public_id]["notes"] == 1
    serialized = str(payload).lower()
    assert "fingerprint" not in serialized
    assert "installation_id" not in serialized
    assert calls == []  # Cross-user aggregates never call the single-book resolver.


@pytest.mark.unit
def test_r2_f1_inventory_rows_counts_and_named_deletions_use_the_live_owner_view(
        session, monkeypatch):
    from datetime import datetime, timezone

    from cps import annotations, ub

    _user(session)
    device = _device(session)
    report = ub.DeviceInventoryReport(
        device_id=device.id, item_count=3, matched_count=2,
        observed_at=datetime.now(timezone.utc),
    )
    session.add(report)
    session.flush()
    visible = ub.DeviceInventoryItem(
        device_id=device.id, lpath="Books/Visible.epub", checksum="1" * 32,
        book_id=5, size=1, mtime=1, last_report_id=report.id,
    )
    excluded = ub.DeviceInventoryItem(
        device_id=device.id, lpath="Books/Excluded.epub", checksum="2" * 32,
        book_id=99, size=2, mtime=2, last_report_id=report.id,
    )
    unmatched = ub.DeviceInventoryItem(
        device_id=device.id, lpath="Books/Unmatched.epub", checksum="3" * 32,
        book_id=None, size=3, mtime=3, last_report_id=report.id,
    )
    session.add_all([visible, excluded, unmatched])
    session.commit()
    _allow_books(monkeypatch, annotations, hidden={99})
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    queued = []
    monkeypatch.setattr(
        annotations.device_capabilities,
        "queue_named_deletion",
        lambda **kwargs: queued.append(kwargs),
    )
    app = Flask(__name__)

    with app.test_request_context(
            f"/api/annotations/devices/{device.public_id}/inventory"):
        payload = annotations.annotation_device_inventory.__wrapped__(
            device.public_id,
        ).get_json()
    assert payload["total"] == payload["device"]["inventory_count"] == 1
    assert [book["book_id"] for book in payload["books"]] == [5]
    assert "Excluded.epub" not in str(payload)
    assert "Unmatched.epub" not in str(payload)

    for item in (excluded, unmatched):
        with app.test_request_context(
                f"/api/annotations/devices/{device.public_id}/inventory/{item.id}/delete",
                method="POST"):
            response, status = annotations.annotation_device_inventory_delete.__wrapped__(
                device.public_id, item.id,
            )
        assert status == 404
        assert response.get_json() == {"error": "inventory_item_not_found"}
    assert queued == []


@pytest.mark.unit
def test_r2_f2_preflight_delete_and_restore_responses_exclude_filtered_books(
        session, monkeypatch):
    from cps import annotations, ub

    _user(session)
    device = _device(session)
    visible = _annotation(
        session, "visible", book_id=5, origin=device.id, assigned=device.id,
    )
    excluded = _annotation(
        session, "excluded", book_id=99, origin=device.id, assigned=device.id,
    )
    session.commit()
    _allow_books(monkeypatch, annotations, hidden={99})

    _found_device, preflight = annotations.device_annotation_counts(
        device.public_id, user_id=7, session=session,
    )
    assert preflight == {"origin_count": 1, "assigned_count": 1}
    _deleted, delete_counts = annotations.soft_delete_annotation_device(
        device.public_id, user_id=7, session=session, commit=session.commit,
    )
    assert delete_counts == preflight
    assert visible.assigned_device_id is None
    assert excluded.assigned_device_id is None

    _restored, restored_count, conflicts = annotations.restore_annotation_device(
        device.public_id, user_id=7, session=session, commit=session.commit,
    )
    session.refresh(visible)
    session.refresh(excluded)
    assert (restored_count, conflicts) == (1, 0)
    assert visible.assigned_device_id == device.id
    assert excluded.assigned_device_id is None
    assert session.query(ub.DeviceRetiredAssignment).filter_by(
        device_id=device.id, annotation_id=excluded.id,
    ).count() == 1


@pytest.mark.unit
def test_r2_f3_missing_device_owner_fails_closed_and_logs(
        session, filtered_library_stub, caplog):
    from cps import annotations

    with caplog.at_level("ERROR"):
        assert annotations._visible_books_for_owner(None, [5, 6]) == {}
        assert annotations._visible_book_scope_for_owner(None, session) == frozenset()
        assert filtered_library_stub([None], session) == {}

    assert "owner unavailable" in caplog.text
    assert "filtered views denied" in caplog.text


@pytest.mark.unit
def test_r2_f4_all_device_collections_are_capped_and_report_true_totals(
        session, monkeypatch):
    from datetime import datetime, timezone

    from cps import admin, annotations, ub

    _user(session)
    devices = [_device(session, label=f"Reader {index:03d}") for index in range(205)]
    now = datetime.now(timezone.utc)
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=devices[0].id, book_id=index + 1,
            progress_percent=float(index), server_modified_at=now,
        )
        for index in range(205)
    ])
    session.commit()
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(admin, "current_user", SimpleNamespace(role_admin=lambda: True))
    app = Flask(__name__)

    with app.test_request_context("/api/annotations/devices"):
        device_page = annotations.annotation_devices_list.__wrapped__().get_json()
    assert len(device_page["devices"]) == 100
    assert device_page["total"] == 205

    with app.test_request_context(
            f"/api/annotations/devices/{devices[0].public_id}/positions"):
        position_page = annotations.annotation_device_positions.__wrapped__(
            devices[0].public_id,
        ).get_json()
    assert len(position_page["positions"]) == 100
    assert position_page["total"] == 205

    with app.test_request_context("/api/admin/devices"):
        admin_page = annotations.annotation_admin_devices().get_json()
    assert len(admin_page["devices"]) == 50
    assert admin_page["total"] == 205

    for path, route in (
        ("/api/annotations/devices?limit=201", annotations.annotation_devices_list.__wrapped__),
        (
            f"/api/annotations/devices/{devices[0].public_id}/positions?limit=201",
            lambda: annotations.annotation_device_positions.__wrapped__(devices[0].public_id),
        ),
        ("/api/admin/devices?limit=201", annotations.annotation_admin_devices),
    ):
        with app.test_request_context(path):
            response, status = route()
        assert status == 400
        assert response.get_json()["error"] == "invalid_pagination"


@pytest.mark.unit
def test_r2_f4_annotation_rows_are_limited_in_sql_before_materialization(
        session, monkeypatch):
    from sqlalchemy import event
    from cps import annotations, ub

    _user(session)
    device = _device(session)
    for index in range(75):
        _annotation(
            session, f"bounded-{index}", book_id=index + 1,
            origin=device.id, annotation_type="highlight",
        )
    session.commit()
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=7))
    monkeypatch.setattr(ub, "session", session)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    event.listen(session.get_bind(), "before_cursor_execute", capture)
    try:
        app = Flask(__name__)
        with app.test_request_context(
                f"/api/annotations/devices/{device.public_id}/annotations?page=1"):
            payload = annotations.annotation_device_annotations.__wrapped__(
                device.public_id,
            ).get_json()
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture)

    assert len(payload["annotations"]) == 50
    row_queries = [
        statement for statement in statements
        if "from annotation" in statement
        and "order by annotation.server_modified_at" in statement
    ]
    assert len(row_queries) == 1
    assert " limit " in row_queries[0]


@pytest.mark.unit
def test_r2_f5_admin_board_query_count_is_constant_as_devices_and_users_grow(
        session, monkeypatch):
    from datetime import datetime, timezone

    from sqlalchemy import event
    from cps import admin, annotations, ub

    first = _user(session, user_id=7)
    first_device = _device(session, user_id=first.id)
    _annotation(session, "first", user_id=first.id, origin=first_device.id,
                annotation_type="highlight")
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(admin, "current_user", SimpleNamespace(role_admin=lambda: True))
    app = Flask(__name__)

    def board_query_count():
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        event.listen(session.get_bind(), "before_cursor_execute", capture)
        try:
            with app.test_request_context("/api/admin/devices?limit=200"):
                response = annotations.annotation_admin_devices()
            assert response.status_code == 200
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", capture)
        return len(statements)

    small_count = board_query_count()
    now = datetime.now(timezone.utc)
    for user_id in range(8, 28):
        owner = _user(session, user_id=user_id)
        device = _device(session, user_id=owner.id)
        _annotation(
            session, f"annotation-{user_id}", user_id=owner.id,
            book_id=user_id, origin=device.id, annotation_type="note",
        )
        session.add(ub.DeviceReadingPosition(
            device_id=device.id, book_id=user_id,
            progress_percent=50, server_modified_at=now,
        ))
        state = ub.KoboAnnotationBookState(
            user_id=owner.id, book_id=user_id, content_id=f"book-{user_id}",
            authority_status="authoritative",
        )
        session.add(state)
        session.flush()
        session.add(ub.KoboAnnotationSeedCapture(
            book_state_id=state.id, device_id=device.id,
            result="accepted", completed_at=now,
        ))
    session.commit()
    large_count = board_query_count()

    assert small_count == large_count
    assert large_count <= 15


@pytest.mark.unit
def test_r2_f6_inactive_kobo_seed_counts_are_zero_and_partial_uses_active_devices(
        session):
    from datetime import datetime, timezone

    from cps import annotations, ub

    owner = _user(session)
    seeded_active = _device(session, label="Seeded active")
    unseeded_active = _device(session, label="Unseeded active")
    inactive = _device(session, label="Retired", active=False)
    state = ub.KoboAnnotationBookState(
        user_id=owner.id, book_id=5, content_id="book-5",
        authority_status="authoritative",
    )
    session.add(state)
    session.flush()
    now = datetime.now(timezone.utc)
    session.add_all([
        ub.KoboAnnotationSeedCapture(
            book_state_id=state.id, device_id=seeded_active.id,
            result="accepted", completed_at=now,
        ),
        ub.KoboAnnotationSeedCapture(
            book_state_id=state.id, device_id=inactive.id,
            result="accepted", completed_at=now,
        ),
    ])
    session.commit()

    board = {
        row["public_id"]: row
        for row in annotations.list_annotation_devices(user_id=7, session=session)
    }
    assert board[seeded_active.public_id]["seeded_books"] == 1
    assert board[unseeded_active.public_id]["unseeded_books"] == 1
    assert board[inactive.public_id]["seeded_books"] == 0
    assert board[inactive.public_id]["unseeded_books"] == 0
    assert board[inactive.public_id]["authority"]["books_partially_seeded"] == 1
