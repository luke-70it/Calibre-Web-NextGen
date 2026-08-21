# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for the destructive CWA Settings reset (#1694)."""

import inspect
from pathlib import Path

import flask
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CWA_SETTINGS_TEMPLATE = REPO_ROOT / "cps" / "templates" / "cwa_settings.html"


class _SettingsDB:
    defaults = {"auto_convert_target_format": "epub"}
    stored = {"auto_convert_target_format": "mobi"}
    reset_calls = []
    update_calls = []

    def __init__(self):
        self.cwa_default_settings = dict(self.defaults)
        self.cwa_settings = dict(self.stored)

    def get_cwa_settings(self):
        return dict(self.stored)

    def update_cwa_settings(self, settings):
        self.__class__.stored.update(settings)
        self.__class__.update_calls.append(dict(settings))

    def set_default_settings(self, force=False):
        self.__class__.stored = dict(self.defaults)
        self.__class__.reset_calls.append(force)

    def execute_write(self, _query, _params=()):
        return None


@pytest.fixture
def settings_client(monkeypatch):
    from cps import cwa_functions, schedule

    _SettingsDB.stored = {"auto_convert_target_format": "mobi"}
    _SettingsDB.reset_calls = []
    _SettingsDB.update_calls = []

    monkeypatch.setattr(cwa_functions, "CWA_DB", _SettingsDB)
    monkeypatch.setattr(cwa_functions, "INTEGER_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "FLOAT_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "JSON_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "_", lambda text, **_kwargs: text)
    monkeypatch.setattr(cwa_functions.config, "config_kobo_sync_magic_shelves", False, raising=False)
    monkeypatch.setattr(cwa_functions.config, "config_hardcover_sync", False, raising=False)
    monkeypatch.setattr(cwa_functions.config, "save", lambda: None)
    monkeypatch.setattr(cwa_functions.config, "resolved_hardcover_token", lambda: None)
    monkeypatch.setattr(schedule, "refresh_hardcover_auto_fetch", lambda: None)
    monkeypatch.setattr(cwa_functions, "get_next_duplicate_scan_run", lambda _settings: None)
    monkeypatch.setattr(
        cwa_functions,
        "render_title_template",
        lambda _template, **context: {"settings": context["cwa_settings"]},
    )

    app = flask.Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(cwa_functions.cwa_settings)
    app.view_functions["cwa_settings.set_cwa_settings"] = inspect.unwrap(
        cwa_functions.set_cwa_settings
    )
    return app.test_client()


def test_route_dispatches_stable_actions_without_english_labels(settings_client):
    reset_response = settings_client.post(
        "/cwa-settings",
        data={"settings_action": "reset"},
    )

    assert reset_response.status_code == 200
    assert _SettingsDB.reset_calls == [True]
    assert _SettingsDB.stored == _SettingsDB.defaults

    save_response = settings_client.post(
        "/cwa-settings",
        data={
            "settings_action": "save",
            "auto_convert_target_format": "azw3",
        },
    )

    assert save_response.status_code == 200
    assert _SettingsDB.update_calls[-1]["auto_convert_target_format"] == "azw3"
    assert _SettingsDB.stored["auto_convert_target_format"] == "azw3"


@pytest.mark.parametrize(
    ("legacy_label", "expected_target", "expected_reset_calls"),
    [
        ("Submit", "azw3", []),
        ("Apply Default Settings", "epub", [True]),
    ],
)
def test_route_keeps_cached_english_forms_working(
    settings_client, legacy_label, expected_target, expected_reset_calls
):
    response = settings_client.post(
        "/cwa-settings",
        data={
            "submit_button": legacy_label,
            "auto_convert_target_format": "azw3",
        },
    )

    assert response.status_code == 200
    assert _SettingsDB.stored["auto_convert_target_format"] == expected_target
    assert _SettingsDB.reset_calls == expected_reset_calls


def test_template_makes_save_primary_and_confirms_the_full_reset():
    source = CWA_SETTINGS_TEMPLATE.read_text(encoding="utf-8")
    reset = source.index('name="settings_action" value="reset"')
    save = source.index('name="settings_action" value="save"')

    assert reset < save, "Reset must remain left of the rightmost Save action"
    assert 'value="reset" class="btn btn-default"' in source
    assert 'value="save" class="btn btn-primary"' in source
    assert "Reset every CWA setting to its default?" in source
    assert "All of your current CWA settings will be permanently lost." in source
    assert 'onclick="return confirm(this.dataset.confirm);"' in source
    assert "{{ _('Reset All CWA Settings') }}" in source
    assert "{{ _('Save') }}" in source
