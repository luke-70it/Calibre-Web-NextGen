# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Named per-user boolean preferences stored in User.view_settings.

The facility is deliberately generic, but this pass registers only
``discover_hidden``. A null value in /me means "not adopted yet"; true/false is
authoritative server state.
"""
import inspect
import json
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FRONTEND = _ROOT / "frontend" / "src"


def _serializable_user(view_settings):
    from cps import constants, ub

    user = ub.User()
    user.id = 7
    user.name = "reader"
    user.locale = "en"
    user.theme = 1
    user.ui_font_body = ""
    user.ui_font_display = ""
    user.role = constants.ROLE_USER
    user.sidebar_view = constants.ADMIN_USER_SIDEBAR
    user.view_settings = view_settings
    user.kobo_only_shelves_sync = 0
    return user


def test_me_serializes_named_preference_and_unset_state():
    from cps.api.serializers import serialize_user

    assert serialize_user(_serializable_user({}))["preferences"] == {
        "discover_hidden": None,
    }
    assert serialize_user(_serializable_user({
        "preferences": {"discover_hidden": True},
    }))["preferences"] == {"discover_hidden": True}


def test_me_ignores_malformed_stored_preference():
    from cps.api.serializers import serialize_user

    payload = serialize_user(_serializable_user({
        "preferences": {"discover_hidden": "yes"},
    }))
    assert payload["preferences"]["discover_hidden"] is None


class _FakeUser:
    def __init__(self, *, anonymous=False, view_settings=None):
        self.is_authenticated = not anonymous
        self.is_anonymous = anonymous
        self.view_settings = view_settings if view_settings is not None else {}

    def get_view_property(self, page, prop):
        section = self.view_settings.get(page)
        return section.get(prop) if isinstance(section, dict) else None

    def set_view_property(self, page, prop, value, commit=True):
        assert commit is False, "the endpoint must own the transaction"
        self.view_settings.setdefault(page, {})[prop] = value


def _ctx(body):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context(
        "/api/v1/account/preferences", method="POST", json=body,
        content_type="application/json",
    )


def _call(body, user, session=None):
    from cps.api import account

    session = session or MagicMock()
    with _ctx(body), \
         patch.object(account, "current_user", user), \
         patch.object(account.ub, "session", session):
        response = inspect.unwrap(account.update_named_preferences)()
    return response, session


def _status(response):
    return response[1] if isinstance(response, tuple) else response.status_code


def _json(response):
    response = response[0] if isinstance(response, tuple) else response
    return json.loads(response.get_data(as_text=True))


def test_endpoint_persists_known_boolean_and_returns_state():
    user = _FakeUser()
    response, session = _call({"preferences": {"discover_hidden": True}}, user)

    assert _status(response) == 200
    assert user.view_settings == {"preferences": {"discover_hidden": True}}
    assert _json(response) == {"preferences": {"discover_hidden": True}}
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("body", [
    {},
    {"preferences": {}},
    {"preferences": []},
    {"preferences": {"unknown": True}},
    {"preferences": {"discover_hidden": 1}},
    {"preferences": {"discover_hidden": "true"}},
])
def test_endpoint_rejects_invalid_updates_without_mutating(body):
    user = _FakeUser()
    response, session = _call(body, user)

    assert _status(response) == 400
    assert user.view_settings == {}
    session.commit.assert_not_called()


def test_endpoint_rolls_back_commit_failure():
    user = _FakeUser()
    session = MagicMock()
    session.commit.side_effect = RuntimeError("database locked")

    response, session = _call(
        {"preferences": {"discover_hidden": False}}, user, session)

    assert _status(response) == 500
    session.rollback.assert_called_once_with()


def test_endpoint_rejects_guest_without_writing():
    user = _FakeUser(anonymous=True)
    response, session = _call(
        {"preferences": {"discover_hidden": True}}, user)

    assert _status(response) == 401
    session.commit.assert_not_called()


def test_frontend_uses_generic_named_preference_hook_for_discover():
    hook = _FRONTEND / "lib" / "useNamedPreference.ts"
    assert hook.is_file()
    hook_src = hook.read_text(encoding="utf-8")
    catalog_src = (_FRONTEND / "pages" / "Catalog.tsx").read_text(encoding="utf-8")
    queries_src = (_FRONTEND / "lib" / "queries.ts").read_text(encoding="utf-8")

    assert "useNamedPreference" in catalog_src
    assert "discover_hidden" in catalog_src
    assert "cwng_discover_hidden_v1" in catalog_src
    assert "/account/preferences" in queries_src
    assert "role?.anonymous" in hook_src
    assert "localStorage" in hook_src
