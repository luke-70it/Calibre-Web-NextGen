# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo pairing API: token compatibility, auth boundaries and HTTP contract."""

import inspect
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest


def _user(*, user_id=1, authenticated=True, anonymous=False, admin=False):
    return SimpleNamespace(
        id=user_id,
        is_authenticated=authenticated,
        is_anonymous=anonymous,
        role_admin=lambda: admin,
    )


def _ub(*, target_exists=True):
    value = SimpleNamespace(id=1) if target_exists else None
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = value
    return SimpleNamespace(session=session, User=MagicMock())


def _ctx(path, method="GET", host="books.example.test"):
    app = flask.Flask(__name__)
    app.config["SERVER_NAME"] = host
    app.add_url_rule(
        "/kobo/<auth_token>",
        endpoint="kobo.TopLevelEndpoint",
        view_func=lambda auth_token: auth_token,
    )
    return app.test_request_context(path, method=method, base_url=f"https://{host}")


def _json(response):
    raw = response[0] if isinstance(response, tuple) else response
    return json.loads(raw.get_data())


@pytest.mark.unit
def test_anonymous_browse_user_cannot_view_or_create_token():
    from cps.api import kobo_pairing as mod
    for method, view in (("GET", mod.get_kobo_sync_token), ("POST", mod.create_kobo_sync_token)):
        with _ctx("/api/v1/account/kobo-sync-token", method=method), \
                patch.object(mod, "current_user", _user(authenticated=False, anonymous=True)):
            response = inspect.unwrap(view)()
        assert response[1] == 401
        assert _json(response)["error"]["code"] == "unauthorized"


@pytest.mark.unit
@pytest.mark.parametrize("view_name,method", [
    ("get_kobo_sync_token", "GET"),
    ("create_kobo_sync_token", "POST"),
    ("delete_kobo_sync_token", "DELETE"),
])
def test_non_admin_cannot_manage_another_users_token(view_name, method):
    from cps.api import kobo_pairing as mod
    view = getattr(mod, view_name)
    with _ctx("/api/v1/admin/users/2/kobo-sync-token", method=method), \
            patch.object(mod, "current_user", _user(user_id=1)), \
            patch.object(mod, "ub", _ub()):
        response = inspect.unwrap(view)(2)
    assert response[1] == 403
    assert _json(response)["error"]["code"] == "forbidden"


@pytest.mark.unit
def test_missing_admin_target_is_404_without_minting():
    from cps.api import kobo_pairing as mod
    create = MagicMock()
    with _ctx("/api/v1/admin/users/404/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user(admin=True)), \
            patch.object(mod, "ub", _ub(target_exists=False)), \
            patch.object(mod, "create_or_view_auth_token", create):
        response = inspect.unwrap(mod.create_kobo_sync_token)(404)
    assert response[1] == 404
    create.assert_not_called()


@pytest.mark.unit
def test_disabled_kobo_sync_refuses_token_lifecycle():
    from cps.api import kobo_pairing as mod
    with _ctx("/api/v1/account/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=False)):
        response = inspect.unwrap(mod.create_kobo_sync_token)()
    assert response[1] == 409
    assert _json(response)["error"]["code"] == "kobo_sync_disabled"


@pytest.mark.unit
def test_get_views_existing_state_without_creating():
    from cps.api import kobo_pairing as mod
    find = MagicMock(return_value=None)
    create = MagicMock()
    with _ctx("/api/v1/account/kobo-sync-token"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "find_auth_token", find), \
            patch.object(mod, "create_or_view_auth_token", create):
        response = inspect.unwrap(mod.get_kobo_sync_token)()
    assert _json(response) == {
        "configured": False,
        "is_localhost": False,
        "server_url": "https://books.example.test",
        "sync_url": None,
        "user_id": 1,
    }
    find.assert_called_once_with(1)
    create.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("created,status", [(True, 201), (False, 200)])
def test_create_or_view_returns_tokenized_kobo_url(created, status):
    from cps.api import kobo_pairing as mod
    row = SimpleNamespace(auth_token="a" * 32)
    with _ctx("/api/v1/account/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "create_or_view_auth_token", return_value=(row, created, True)) as create:
        response = inspect.unwrap(mod.create_kobo_sync_token)()
    assert response[1] == status
    body = _json(response)
    assert body["configured"] is True
    assert body["sync_url"] == f"https://books.example.test/kobo/{'a' * 32}"
    assert body["server_url"] == "https://books.example.test"
    assert response[0].headers["Cache-Control"] == "private, no-store"
    create.assert_called_once_with(1)


@pytest.mark.unit
def test_admin_can_create_for_another_user_only_on_admin_route():
    from cps.api import kobo_pairing as mod
    row = SimpleNamespace(auth_token="b" * 32)
    with _ctx("/api/v1/admin/users/7/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user(user_id=1, admin=True)), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "create_or_view_auth_token", return_value=(row, True, True)) as create:
        response = inspect.unwrap(mod.create_kobo_sync_token)(7)
    assert response[1] == 201
    assert _json(response)["user_id"] == 7
    create.assert_called_once_with(7)


@pytest.mark.unit
def test_create_commit_failure_returns_500_without_putting_token_in_error():
    from cps.api import kobo_pairing as mod
    secret = "c" * 32
    row = SimpleNamespace(auth_token=secret)
    with _ctx("/api/v1/account/kobo-sync-token", method="POST"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "create_or_view_auth_token", return_value=(row, True, False)):
        response = inspect.unwrap(mod.create_kobo_sync_token)()
    assert response[1] == 500
    assert secret not in response[0].get_data(as_text=True)


@pytest.mark.unit
@pytest.mark.parametrize("committed,status", [(True, 204), (False, 500)])
def test_delete_reports_whether_revocation_landed(committed, status):
    from cps.api import kobo_pairing as mod
    with _ctx("/api/v1/account/kobo-sync-token", method="DELETE"), \
            patch.object(mod, "current_user", _user()), \
            patch.object(mod, "ub", _ub()), \
            patch.object(mod, "config", SimpleNamespace(config_kobo_sync=True)), \
            patch.object(mod, "revoke_auth_token", return_value=committed) as revoke:
        response = inspect.unwrap(mod.delete_kobo_sync_token)()
    assert response[1] == status
    revoke.assert_called_once_with(1)


@pytest.mark.unit
def test_http_contract_keeps_reads_safe_and_mutations_csrf_eligible():
    """Global CSRFProtect applies to POST/DELETE; GET must never mint a token."""
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.register_blueprint(api_v1)
    methods = {}
    for rule in app.url_map.iter_rules():
        if rule.rule.endswith("/kobo-sync-token"):
            methods.setdefault(rule.rule, set()).update(rule.methods)
    assert methods["/api/v1/account/kobo-sync-token"] >= {"GET", "POST", "DELETE"}
    assert methods["/api/v1/admin/users/<int:user_id>/kobo-sync-token"] >= {
        "GET", "POST", "DELETE",
    }


@pytest.mark.unit
def test_shared_helper_preserves_classic_token_semantics():
    from cps import kobo_auth as mod

    class _RemoteAuthToken:
        # SQLAlchemy models expose these on the class for query expressions.
        user_id = MagicMock()
        token_type = MagicMock()

    created = _RemoteAuthToken()
    fake_ub = SimpleNamespace(
        session=MagicMock(),
        RemoteAuthToken=MagicMock(return_value=created),
        session_commit=MagicMock(return_value=True),
    )
    fake_ub.session.query.return_value.filter.return_value.filter.return_value.first.return_value = None
    with patch.object(mod, "ub", fake_ub), patch.object(mod, "urandom", return_value=b"\x0f" * 16):
        row, was_created, committed = mod.create_or_view_auth_token(9)
    assert row is created
    assert was_created is True and committed is True
    assert created.user_id == 9
    assert created.expiration == datetime.max
    assert created.auth_token == "0f" * 16
    assert created.token_type == 1
    fake_ub.session.add.assert_called_once_with(created)
