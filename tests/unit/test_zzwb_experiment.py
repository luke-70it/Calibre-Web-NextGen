# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contained tests for the never-merge ZZWB hardware experiment rig."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, make_response

import cps.readingservices as rs


PROBE = rs.ZZWB_EXPERIMENT_UUID
FOREIGN = "kobo-store-content"


def test_rig_is_pinned_to_the_live_probe_uuid():
    assert PROBE == "053742ff-9094-43b2-8511-c0763c90ffab"


@pytest.fixture
def app():
    return Flask(__name__)


def _view(function):
    """Strip the auth/config decorator exposed by functools.wraps."""
    return function.__wrapped__


def _stage(
    tmp_path,
    monkeypatch,
    *,
    payload=b'{"annotations":[],"nextPageOffsetToken":null}',
    etag='W/"CWNG:experiment-1:7:0123456789abcdef"',
    armed=True,
):
    monkeypatch.setattr(rs, "ZZWB_EXPERIMENT_DIR", str(tmp_path))
    (tmp_path / "payload.json").write_bytes(payload)
    (tmp_path / "etag.txt").write_text(etag + "\n", encoding="ascii")
    if armed:
        (tmp_path / "ARMED").write_text("armed\n", encoding="utf-8")
    return payload, etag


def test_checkforchanges_reuses_production_exchange_capture(
    app, tmp_path, monkeypatch, caplog,
):
    _stage(tmp_path, monkeypatch)
    outbound = []
    capture_calls = []
    decisions = []
    entries = [
        {
            "ContentId": PROBE,
            "etag": 'W/"device-token"',
            "Authorization": "request-body-secret",
        },
        {"ContentId": FOREIGN, "etag": 'W/"foreign-token"'},
    ]

    monkeypatch.setattr(
        rs,
        "resolve_entitlement_ownership",
        lambda content_id: SimpleNamespace(id=540) if content_id == PROBE else None,
    )

    capture_session = SimpleNamespace(
        add_decision=lambda **decision: decisions.append(decision),
    )

    def _begin_capture(exchange, raw_body, **kwargs):
        capture_calls.append((exchange, raw_body, kwargs))
        return capture_session

    monkeypatch.setattr(rs, "_begin_exchange_capture", _begin_capture)

    def _proxy(*, data, capture_session=None):
        assert capture_session is not None
        outbound.extend(json.loads(data))
        response = jsonify([FOREIGN])
        response.headers["Set-Cookie"] = "upstream-cookie-secret"
        response.headers["ETag"] = 'W/"upstream-result"'
        return response

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    with app.test_request_context(
        "/api/v3/content/checkforchanges",
        method="POST",
        json=entries,
        headers={"Authorization": "header-secret", "X-Kobo-UserKey": "user-key-secret"},
    ), caplog.at_level("WARNING"):
        response = _view(rs.handle_check_for_changes)()

    assert outbound == [entries[1]]
    assert response.status_code == 200
    assert response.get_json() == [FOREIGN, PROBE]
    assert len(capture_calls) == 1
    assert capture_calls[0][0] == "checkforchanges"
    assert json.loads(capture_calls[0][1]) == entries
    assert decisions == [
        {
            "stage": "device_request", "index": 0, "content_id": PROBE,
            "ownership": "owned", "authority_status": "unavailable",
            "action": "suppressed",
        },
        {
            "stage": "device_request", "index": 1, "content_id": FOREIGN,
            "ownership": "unowned", "authority_status": None,
            "action": "proxied",
        },
        {
            "stage": "upstream_response", "index": 0, "content_id": FOREIGN,
            "ownership": "unowned", "authority_status": None,
            "action": "returned",
        },
    ]
    assert "ZZWB checkforchanges" not in caplog.text
    captured = caplog.text
    assert "request-body-secret" not in captured
    assert "header-secret" not in captured
    assert "user-key-secret" not in captured
    assert "upstream-cookie-secret" not in captured


def test_disabled_production_capture_does_not_change_armed_probe_answer(
    app, tmp_path, monkeypatch,
):
    _stage(tmp_path, monkeypatch)
    entries = [{"ContentId": PROBE, "etag": 'W/"device-token"'}]
    monkeypatch.setattr(
        rs,
        "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=540),
    )
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("all-owned probe batch must not contact upstream"),
    )
    monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_args, **_kwargs: None)
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == [PROBE]


def test_checkforchanges_capture_is_inert_without_probe_arm(
    app, tmp_path, monkeypatch, caplog,
):
    monkeypatch.setattr(rs, "ZZWB_EXPERIMENT_DIR", str(tmp_path))
    entries = [{"ContentId": FOREIGN, "etag": 'W/"foreign-token"'}]
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services", lambda **_kwargs: jsonify([FOREIGN]),
    )

    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ), caplog.at_level("WARNING"):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == [FOREIGN]
    assert "ZZWB checkforchanges" not in caplog.text


def test_probe_get_serves_exact_dynamic_staged_body_and_etag_without_upstream(
    app, tmp_path, monkeypatch,
):
    first_body, first_etag = _stage(tmp_path, monkeypatch)
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda: pytest.fail("a valid staged GET must never contact upstream"),
    )

    def _get():
        with app.test_request_context(
            f"/api/v3/content/{PROBE}/annotations?limit=100",
            method="GET",
            headers={"If-None-Match": 'W/"0"'},
        ):
            return _view(rs.handle_annotations)(PROBE)

    first = _get()
    assert first.status_code == 200
    assert first.get_data() == first_body
    assert first.headers["Content-Type"] == "application/json"
    assert first.headers["ETag"] == first_etag

    second_body = b'{"annotations":[{"id":"second-cycle"}],"nextPageOffsetToken":null}'
    second_etag = 'W/"CWNG:experiment-2:8:fedcba9876543210"'
    (tmp_path / "payload.json").write_bytes(second_body)
    (tmp_path / "etag.txt").write_text(second_etag + "\n", encoding="ascii")

    second = _get()
    assert second.status_code == 200
    assert second.get_data() == second_body
    assert second.headers["ETag"] == second_etag

    # Cycle R's second leg uses the same stage files and the exact empty token.
    rollback_body = b'{"annotations":[],"nextPageOffsetToken":null}'
    (tmp_path / "payload.json").write_bytes(rollback_body)
    (tmp_path / "etag.txt").write_text('W/"0"\n', encoding="ascii")
    rollback = _get()
    assert rollback.status_code == 200
    assert rollback.get_data() == rollback_body
    assert rollback.headers["ETag"] == 'W/"0"'


def test_staged_get_attaches_the_existing_exchange_capture_before_answering(
    app, tmp_path, monkeypatch,
):
    payload, etag = _stage(tmp_path, monkeypatch)
    capture_calls = []
    capture_session = object()

    def _begin_capture(exchange, raw_body, **kwargs):
        capture_calls.append((exchange, raw_body, kwargs))
        return capture_session

    monkeypatch.setattr(rs, "_begin_exchange_capture", _begin_capture)
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("staged GET must not create an upstream capture leg"),
    )

    with app.test_request_context(
        f"/api/v3/content/{PROBE}/annotations?limit=100", method="GET",
    ):
        response = _view(rs.handle_annotations)(PROBE)

    assert response.status_code == 200
    assert response.get_data() == payload
    assert response.headers["ETag"] == etag
    assert capture_calls == [
        (
            "annotations_get", b"",
            {"authentication": "authenticated", "user_id": None},
        ),
    ]


def test_probe_is_not_named_until_arm_payload_and_etag_are_all_valid(
    app, tmp_path, monkeypatch,
):
    monkeypatch.setattr(rs, "ZZWB_EXPERIMENT_DIR", str(tmp_path))
    monkeypatch.setattr(
        rs,
        "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=540),
    )
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("owned probe must never be forwarded upstream"),
    )

    def _check():
        with app.test_request_context(
            "/api/v3/content/checkforchanges",
            method="POST",
            json=[{"ContentId": PROBE, "etag": 'W/"0"'}],
        ):
            return _view(rs.handle_check_for_changes)().get_json()

    assert _check() == []
    (tmp_path / "ARMED").write_text("armed\n", encoding="utf-8")
    assert _check() == []
    (tmp_path / "payload.json").write_text("not-json", encoding="utf-8")
    (tmp_path / "etag.txt").write_text("not-an-http-etag\n", encoding="ascii")
    assert _check() == []
    (tmp_path / "payload.json").write_text(
        '{"annotations":[],"nextPageOffsetToken":null}', encoding="utf-8",
    )
    assert _check() == []
    (tmp_path / "etag.txt").write_text(
        'W/"CWNG:experiment-1:7:0123456789abcdef"\n', encoding="ascii",
    )
    (tmp_path / "payload.json").write_text(
        '{"wrong":[],"nextPageOffsetToken":null}', encoding="utf-8",
    )
    assert _check() == []
    (tmp_path / "payload.json").write_text(
        '{"annotations":[],"nextPageOffsetToken":null}', encoding="utf-8",
    )
    assert _check() == [PROBE]


def test_unarmed_annotation_get_is_byte_transparent_for_probe_and_foreign(
    app, tmp_path, monkeypatch,
):
    monkeypatch.setattr(rs, "ZZWB_EXPERIMENT_DIR", str(tmp_path))
    responses = []

    def _proxy():
        response = make_response(b"upstream-wire-bytes", 207)
        response.headers["ETag"] = 'W/"upstream"'
        responses.append(response)
        return response

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    for content_id in (PROBE, FOREIGN):
        with app.test_request_context(
            f"/api/v3/content/{content_id}/annotations", method="GET",
        ):
            actual = _view(rs.handle_annotations)(content_id)
        expected = responses[-1]
        assert actual is expected
        assert actual.status_code == 207
        assert actual.get_data() == b"upstream-wire-bytes"
        assert actual.headers["ETag"] == 'W/"upstream"'


def test_foreign_get_short_circuits_before_arming_or_stage_access(
    app, monkeypatch,
):
    sentinel = object()
    monkeypatch.setattr(
        rs,
        "_zzwb_armed",
        lambda: pytest.fail("foreign ContentId touched the arming directory"),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)

    with app.test_request_context(
        f"/api/v3/content/{FOREIGN}/annotations", method="GET",
    ):
        assert _view(rs.handle_annotations)(FOREIGN) is sentinel


def test_complete_stage_files_without_arm_are_byte_transparent(
    app, tmp_path, monkeypatch,
):
    _stage(tmp_path, monkeypatch, armed=False)
    proxied = []

    def _proxy():
        response = make_response(b"origin-main-proxy-response", 206)
        response.headers["ETag"] = 'W/"upstream"'
        proxied.append(response)
        return response

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)

    with app.test_request_context(
        f"/api/v3/content/{PROBE}/annotations", method="GET",
    ):
        actual = _view(rs.handle_annotations)(PROBE)

    assert actual is proxied[0]
    assert actual.status_code == 206
    assert actual.get_data() == b"origin-main-proxy-response"
    assert actual.headers["ETag"] == 'W/"upstream"'


def test_armed_stage_does_not_change_foreign_checkforchanges_path(
    app, tmp_path, monkeypatch, caplog,
):
    _stage(tmp_path, monkeypatch)
    entries = [{"ContentId": FOREIGN, "etag": 'W/"foreign-token"'}]
    forwarded = []
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)

    def _proxy(*, data):
        forwarded.append(data)
        return jsonify([FOREIGN])

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ), caplog.at_level("WARNING"):
        response = _view(rs.handle_check_for_changes)()

    assert forwarded == [json.dumps(entries, separators=(",", ":")).encode("utf-8")]
    assert response.status_code == 200
    assert response.get_json() == [FOREIGN]
    assert "ZZWB checkforchanges" not in caplog.text


def test_foreign_checkforchanges_short_circuits_before_arming_directory(
    app, monkeypatch,
):
    entries = [{"ContentId": FOREIGN, "etag": 'W/"foreign-token"'}]
    monkeypatch.setattr(
        rs,
        "_zzwb_armed",
        lambda: pytest.fail("foreign ContentId touched the arming directory"),
    )
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services", lambda **_kwargs: jsonify([FOREIGN]),
    )

    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_data() == b'["kobo-store-content"]\n'
