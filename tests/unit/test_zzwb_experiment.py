# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contained tests for the never-merge ZZWB hardware experiment rig."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify

import cps.readingservices as rs


PROBE = rs.ZZWB_EXPERIMENT_UUID
FOREIGN = "kobo-store-content"


@pytest.fixture
def app():
    return Flask(__name__)


def _view(function):
    """Strip the auth/config decorator exposed by functools.wraps."""
    return function.__wrapped__


def _arm(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "ZZWB_EXPERIMENT_DIR", str(tmp_path))
    (tmp_path / "ARMED").write_text("armed\n", encoding="utf-8")


def _capture_records(caplog):
    prefix = "ZZWB checkforchanges "
    return [
        json.loads(record.getMessage()[len(prefix):])
        for record in caplog.records
        if record.getMessage().startswith(prefix)
    ]


def test_checkforchanges_capture_records_four_exact_redacted_phases(
    app, tmp_path, monkeypatch, caplog,
):
    _arm(tmp_path, monkeypatch)
    outbound = []
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
        lambda content_id: SimpleNamespace(id=543) if content_id == PROBE else None,
    )

    def _proxy(*, data):
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
    assert _capture_records(caplog) == [
        {
            "phase": "request",
            "count": 2,
            "entries": [
                {"order": 0, "ContentId": PROBE, "etag": 'W/"device-token"'},
                {"order": 1, "ContentId": FOREIGN, "etag": 'W/"foreign-token"'},
            ],
        },
        {
            "phase": "forwarded",
            "count": 1,
            "entries": [
                {"order": 0, "ContentId": FOREIGN, "etag": 'W/"foreign-token"'},
            ],
        },
        {
            "phase": "upstream",
            "status": 200,
            "headers": {"Content-Type": "application/json", "ETag": 'W/"upstream-result"'},
            "body": [FOREIGN],
        },
        {
            "phase": "final",
            "status": 200,
            "body": [FOREIGN, PROBE],
        },
    ]
    captured = caplog.text
    assert "request-body-secret" not in captured
    assert "header-secret" not in captured
    assert "user-key-secret" not in captured
    assert "upstream-cookie-secret" not in captured


def test_checkforchanges_capture_logger_failure_cannot_fail_observed_request(
    app, tmp_path, monkeypatch,
):
    _arm(tmp_path, monkeypatch)
    entries = [{"ContentId": PROBE, "etag": 'W/"device-token"'}]
    monkeypatch.setattr(
        rs,
        "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=543),
    )
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("all-owned probe batch must not contact upstream"),
    )
    original_warning = rs.log.warning

    def _raising_warning(message, *args, **kwargs):
        if message == "ZZWB checkforchanges %s":
            raise RuntimeError("capture sink unavailable")
        return original_warning(message, *args, **kwargs)

    monkeypatch.setattr(rs.log, "warning", _raising_warning)
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
    assert _capture_records(caplog) == []
