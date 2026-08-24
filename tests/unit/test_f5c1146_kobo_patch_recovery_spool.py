# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""F-5c1146: stage Kobo PATCH bytes before parsing or dispatch."""

from __future__ import annotations

import importlib
import inspect
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import cps.readingservices as rs


BOOK_UUID = "9e5251ad-d530-4e58-9121-8b8336099fdd"
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_PATCH = (
    b'{"updatedAnnotations":[{"id":"annotation-1","type":"highlight",'
    b'"highlightedText":"private words"}],"deletedAnnotationIds":[]}'
)


def _module():
    return importlib.import_module("cps.services.kobo_patch_spool")


def _root(monkeypatch, tmp_path):
    spool = _module()
    root = tmp_path / "private-patch-spool"
    monkeypatch.setattr(spool, "_spool_root", lambda: root)
    return spool, root


def _records(spool, root):
    paths = sorted(root.glob("patch-*.json.gz"))
    return [(path, spool.load_spooled_patch(path)) for path in paths]


def _app(monkeypatch, *, dispatch):
    app = Flask(__name__)

    # GET is registered too: the body-read guard has to behave DIFFERENTLY for
    # GET than for PATCH, and a PATCH-only app makes that assertion vacuous -
    # the GET would 405 and trivially satisfy "not 503".
    @app.route("/annotations/<content_id>", methods=["GET", "PATCH"])
    def annotations(content_id):
        return rs.handle_annotations.__wrapped__(content_id)

    book = SimpleNamespace(id=347, title="Flatland", identifiers=[])
    user = SimpleNamespace(id=7, name="test-user", is_authenticated=True)
    monkeypatch.setattr(rs, "current_user", user)
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "cps.services.annotation_sync.dispatch_annotation_sync", dispatch,
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: app.response_class(
            b'{"upstream":"accepted"}', status=207, headers={"X-Upstream": "same"},
        ),
    )
    return app


@pytest.mark.unit
def test_patch_spool_is_durable_before_parse_and_dispatch(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    source = inspect.getsource(rs.handle_annotations.__wrapped__)
    assert source.index("_stage_patch_for_recovery") < source.index("request.get_json")
    assert source.index("_stage_patch_for_recovery") < source.index("dispatch_annotation_sync")

    def _dispatch(*_args, **_kwargs):
        [(_path, record)] = _records(spool, root)
        assert record["body"] == RAW_PATCH
        assert record["dispatch_status"] == "staged"

    app = _app(monkeypatch, dispatch=_dispatch)
    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 207
    assert response.get_data() == b'{"upstream":"accepted"}'
    [(path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["body_sha256"] == spool.sha256_bytes(RAW_PATCH)
    assert record["dispatch_status"] == "dispatch_completed"
    assert record["user_id"] == 7
    assert record["entitlement_id"] == BOOK_UUID
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_dispatch_exception_spools_the_body_and_still_refuses_to_acknowledge(
    monkeypatch, tmp_path,
):
    """The spool must not soften the #1825 refusal.

    Spooling makes a lost delta recoverable server-side, which is why the
    response code matters less than it did.  It does not make the PATCH stored,
    so CWNG must still answer 503 rather than let the device retire a delta it
    will never re-send.  Asserting 207 here would silently revert F-5c1146.
    """
    spool, root = _root(monkeypatch, tmp_path)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("dispatch exploded")

    app = _app(monkeypatch, dispatch=_raise)
    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 503
    # not the proxied upstream body: we are refusing, not relaying an acceptance
    assert b"upstream" not in response.get_data()
    [(path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "dispatch_exception"
    assert list(spool.iter_replay_candidates()) == [path]
    serialized = json.dumps({key: value for key, value in record.items() if key != "body"})
    assert "private words" not in serialized


@pytest.mark.unit
def test_parse_exception_still_leaves_staged_replay_candidate(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    app = _app(monkeypatch, dispatch=lambda *_args, **_kwargs: None)
    original = app.request_class.get_json

    def _raise_parse(self, *args, **kwargs):
        del self, args, kwargs
        raise ValueError("parser failed")

    monkeypatch.setattr(app.request_class, "get_json", _raise_parse)
    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )
    monkeypatch.setattr(app.request_class, "get_json", original)

    # Same contract as the dispatch-exception case: the body is recoverable, but
    # nothing was stored, so the device must not be told the delta landed.
    assert response.status_code == 503
    [(_path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "dispatch_exception"


@pytest.mark.unit
def test_spool_failure_cannot_change_patch_response_or_dispatch(monkeypatch, tmp_path):
    spool, _root_path = _root(monkeypatch, tmp_path)
    dispatched = []
    app = _app(
        monkeypatch,
        dispatch=lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(
        spool, "stage_patch",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("read-only config")),
    )

    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 207
    assert response.get_data() == b'{"upstream":"accepted"}'
    assert len(dispatched) == 1


@pytest.mark.unit
def test_existing_ownership_unknown_503_is_unchanged_and_body_is_spooled(
    monkeypatch, tmp_path,
):
    spool, root = _root(monkeypatch, tmp_path)
    app = Flask(__name__)

    @app.patch("/annotations/<content_id>")
    def annotations(content_id):
        return rs.handle_annotations.__wrapped__(content_id)

    monkeypatch.setattr(rs, "current_user", SimpleNamespace(id=7, is_authenticated=True))
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership", lambda _content_id: rs.OWNERSHIP_UNKNOWN,
    )
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("the existing 503 branch must not proxy"),
    )

    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "Annotation capture temporarily unavailable"}
    [(_path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "staged"


@pytest.mark.unit
def test_patch_spool_is_bounded_and_never_stores_request_headers(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_FILES", 2)
    monkeypatch.setattr(spool, "MAX_TOTAL_BYTES", 1024 * 1024)

    for index in range(4):
        ticket = spool.stage_patch(
            raw_body=f'{{"updatedAnnotations":[],"index":{index}}}'.encode(),
            entitlement_id=f"book-{index}", user_id=7, origin_device_id=None,
        )
        assert ticket is not None

    records = _records(spool, root)
    assert len(records) == 2
    assert [record["entitlement_id"] for _path, record in records] == ["book-2", "book-3"]
    for _path, record in records:
        assert "headers" not in record
        assert "authorization" not in json.dumps(
            {key: value for key, value in record.items() if key != "body"}
        ).lower()
    assert sum(path.stat().st_size for path in root.glob("patch-*.json.gz")) \
        <= spool.MAX_TOTAL_BYTES


@pytest.mark.unit
def test_replay_candidate_predicate_distinguishes_completed_from_lost():
    spool = _module()
    assert spool.is_replay_candidate("staged") is True
    assert spool.is_replay_candidate("dispatch_exception") is True
    assert spool.is_replay_candidate("dispatch_completed") is False


@pytest.mark.unit
def test_oversized_patch_is_not_partially_spooled(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_BODY_BYTES", 8)

    ticket = spool.stage_patch(
        raw_body=b"123456789", entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert ticket is None
    assert not list(root.glob("patch-*.json.gz")) if root.exists() else True


@pytest.mark.unit
def test_private_observability_root_is_git_ignored():
    spool = _module()
    private_parent = spool._spool_root().parent.name
    patterns = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    accepted = {
        f"/{private_parent}/", f"/{private_parent}",
        f"{private_parent}/", private_parent,
    }
    assert patterns & accepted, (
        f"{private_parent!r} can contain raw annotation text and must be git-ignored"
    )


@pytest.mark.unit
def test_private_observability_root_is_excluded_from_docker_context():
    spool = _module()
    private_parent = spool._spool_root().parent.name
    patterns = {
        line.strip().rstrip("/")
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert private_parent in patterns, (
        f"{private_parent!r} can contain raw annotation text and must not enter images"
    )


@pytest.mark.unit
def test_unreadable_patch_body_still_refuses_but_unreadable_get_body_does_not(
    monkeypatch, tmp_path,
):
    """Moving the body read earlier must not change either hazard.

    The read moved out of the PATCH try-block so the exchange capture could see
    the bytes.  Two things must survive that move:
      * a PATCH whose body cannot be read is still refused with 503, because
        nothing was stored (F-5c1146 / #1825);
      * a GET whose body cannot be read is NOT refused, because a 503 on the
        annotations GET is a measured way to make Nickel empty the book's local
        annotation set.
    """
    _root(monkeypatch, tmp_path)
    app = _app(monkeypatch, dispatch=lambda *_a, **_k: None)

    def _unreadable(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("body read exploded")

    monkeypatch.setattr(app.request_class, "get_data", _unreadable)

    patch_response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )
    assert patch_response.status_code == 503

    get_response = app.test_client().get(f"/annotations/{BOOK_UUID}")
    assert get_response.status_code != 503
