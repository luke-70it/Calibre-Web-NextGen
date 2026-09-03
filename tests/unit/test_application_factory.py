# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

import cps
from cps.reverseproxy import ReverseProxied


def _stub_real_bootstrap(monkeypatch):
    """Leave Flask construction real while isolating process and storage work."""
    from cps import calibre_init, cw_babel, helper, schedule

    monkeypatch.setattr(cps, "app", cps.app)
    monkeypatch.setattr(cps.cli_param, "init", lambda: None)
    monkeypatch.setattr(cps.cli_param, "settings_path", "/tmp/factory-app.db")
    monkeypatch.setattr(cps.cli_param, "user_credentials", None)
    monkeypatch.setattr(cps.cli_param, "memory_backend", False)
    monkeypatch.setattr(cps.cli_param, "dry_run", False)
    monkeypatch.setattr(cps.ub, "init_db", lambda _path: None)
    monkeypatch.setattr(cps.ub, "session", SimpleNamespace(bind=None))
    monkeypatch.setattr(cps.ub, "password_change", lambda _credentials: None)
    monkeypatch.setattr(cps.ub, "backfill_annotation_content_ids", lambda *_args: None)
    monkeypatch.setattr(cps.ub, "oauth_support", False)
    monkeypatch.setattr(
        cps.ub,
        "Anonymous",
        type("FactoryAnonymous", (), {"is_authenticated": False, "is_anonymous": True}),
    )
    monkeypatch.setattr(cps.config_sql, "get_encryption_key", lambda _path: (None, None))
    monkeypatch.setattr(cps.config_sql, "load_configuration", lambda *_args: None)
    monkeypatch.setattr(cps.config_sql, "get_flask_session_key", lambda _session: "test")
    monkeypatch.setattr(cps.config, "init_config", lambda *_args: None)
    config_values = {
        "config_login_type": 0,
        "config_use_https": False,
        "config_oauth_redirect_host": "",
        "config_session": 0,
        "config_ratelimiter": False,
        "config_limiter_uri": "",
        "config_limiter_options": "",
        "config_allow_reverse_proxy_header_login": False,
        "config_reverse_proxy_login_header_name": "",
        "config_goodreads_api_key": "",
        "config_use_goodreads": False,
        "config_use_google_drive": False,
        "config_trustedhosts": "",
        "schedule_reconnect": False,
        "store_calibre_uuid": lambda *_args: None,
    }
    for name, value in config_values.items():
        monkeypatch.setattr(cps.config, name, value, raising=False)

    monkeypatch.setattr(cps, "_ensure_user_profiles_json", lambda: None)
    monkeypatch.setattr(calibre_init, "init_calibre_db_from_config", lambda *_args: None)
    monkeypatch.setattr(cps.calibre_db, "init_db", lambda: None)
    monkeypatch.setattr(cps.calibre_db, "ensure_session", lambda: None)
    monkeypatch.setattr(cps.calibre_db, "_desktop_compat", False)
    monkeypatch.setattr(cps.calibre_db, "session", None)
    monkeypatch.setattr(cps.calibre_db, "session_factory", None)
    monkeypatch.setattr(helper, "scavenge_staged_cover_files", lambda: None)
    monkeypatch.setattr(cps.updater_thread, "init_updater", lambda *_args: None)
    updater_start = MagicMock()
    monkeypatch.setattr(cps.updater_thread, "start", updater_start)
    monkeypatch.setattr(cps, "Principal", lambda _app: None)
    monkeypatch.setattr(cps.lm, "_user_callback", lambda *_args: None)
    monkeypatch.setattr(cps.web_server, "init_app", lambda *_args: None)
    monkeypatch.setattr(cw_babel.babel, "init_app", lambda *_args, **_kwargs: None)
    if hasattr(cw_babel.babel, "localeselector"):
        monkeypatch.setattr(cw_babel.babel, "localeselector", lambda _selector: None)
    monkeypatch.setattr(cps.limiter, "init_app", lambda _app: None)

    scheduled = MagicMock()
    startup = MagicMock()
    monkeypatch.setattr(schedule, "register_scheduled_tasks", scheduled)
    monkeypatch.setattr(schedule, "register_startup_tasks", startup)
    service_bundle = SimpleNamespace(ldap=None, goodreads_support=None)
    return service_bundle, updater_start, scheduled, startup


def _middleware_names(application):
    names = []
    middleware = application.wsgi_app
    seen = set()
    while id(middleware) not in seen:
        seen.add(id(middleware))
        names.append(type(middleware).__name__)
        if isinstance(middleware, ReverseProxied):
            middleware = middleware.app
        elif isinstance(middleware, ProxyFix):
            middleware = middleware.app
        else:
            break
    return names


def _hook_counts(application):
    return {
        "before": sum(len(items) for items in application.before_request_funcs.values()),
        "after": sum(len(items) for items in application.after_request_funcs.values()),
        "teardown_appcontext": len(application.teardown_appcontext_funcs),
        "teardown_request": len(application.teardown_request_funcs.get(None, [])),
        "error_handlers": sum(
            len(exception_map)
            for code_map in application.error_handler_spec.values()
            for exception_map in code_map.values()
        ),
    }


@pytest.mark.unit
def test_factory_constructs_two_independent_apps_without_process_job_duplication(monkeypatch):
    """A second factory call gets its own app but cannot restart process jobs."""
    from cps import web

    services, updater_start, scheduled, startup = _stub_real_bootstrap(monkeypatch)

    first = cps.create_app(cps.config, services)
    first_counts = _hook_counts(first)
    first_jobs = (updater_start.call_count, scheduled.call_count, startup.call_count)

    second = cps.create_app(cps.config, services)
    second_counts = _hook_counts(second)
    second_jobs = (updater_start.call_count, scheduled.call_count, startup.call_count)

    assert first is not second
    assert first_counts == second_counts
    assert len(first.after_request_funcs[None]) == len(set(first.after_request_funcs[None]))
    assert len(second.after_request_funcs[None]) == len(set(second.after_request_funcs[None]))
    for application in (first, second):
        after_hooks = application.after_request_funcs[None]
        assert after_hooks.count(cps.protect_user_specific_catalog_responses) == 1
        assert after_hooks.count(web.add_security_headers) == 1
        assert after_hooks.count(web.add_static_asset_cache_headers) == 1
    assert _middleware_names(first).count("ProxyFix") == 1
    assert _middleware_names(second).count("ProxyFix") == 1
    assert first_jobs == (1, 1, 1)
    assert second_jobs == first_jobs

    first.add_url_rule("/factory-probe", "first_factory_probe", lambda: "first")
    second.add_url_rule("/factory-probe", "second_factory_probe", lambda: "second")
    assert first.test_client().get("/factory-probe").data == b"first"
    assert second.test_client().get("/factory-probe").data == b"second"


@pytest.mark.unit
@pytest.mark.parametrize("kobo_available", [False, True])
def test_register_blueprints_preserves_order_on_each_factory_product(
    monkeypatch, kobo_available
):
    """The extracted seam registers the same ordered route surface on both apps."""
    services, _, _, _ = _stub_real_bootstrap(monkeypatch)
    first = cps.create_app(cps.config, services)
    second = cps.create_app(cps.config, services)

    from cps import kobo as kobo_module
    from cps.main import register_blueprints

    monkeypatch.setattr(kobo_module, "get_kobo_activated", lambda: kobo_available)
    register_blueprints(first)
    register_blueprints(second)

    expected_without_generated_oauth = [
        "switch_theme", "library_refresh", "convert_library", "epub_fixer",
        "cover_enforcer_ui", "cwa_stats", "cwa_check_status", "cwa_settings",
        "cwa_logs", "profile_pictures", "cwa_internal", "search", "tasks",
        "web", "opds", "jinjia", "about", "shelf", "admin", "remotelogin",
        "metadata", "gdrive", "edit-book", "cover_picker", "cover_preview_bp",
        "annotations", "kosync", "duplicates", "api_v1", "spa", "oauth",
    ]
    expected = list(expected_without_generated_oauth)
    if kobo_available:
        expected[-1:-1] = [
            "kobo", "kobo_auth", "readingservices_api_v3",
            "readingservices_userstorage",
        ]
    assert list(first.blueprints) == expected
    assert list(second.blueprints) == expected
    assert len(list(first.url_map.iter_rules())) == len(list(second.url_map.iter_rules()))


@pytest.mark.unit
def test_generated_oauth_blueprints_are_fresh_for_each_app(monkeypatch):
    """Provider blueprints cannot be carried from one factory product to the next."""
    from cps import oauth_bb

    monkeypatch.setattr(oauth_bb.ub, "oauth_support", True)
    monkeypatch.setattr(oauth_bb, "oauthblueprints", [{"stale": True}])

    def generate(application):
        assert oauth_bb.oauthblueprints == []
        from flask import Blueprint
        generated = []
        for name in ("github", "google", "generic"):
            blueprint = Blueprint(name, __name__)
            application.register_blueprint(blueprint, url_prefix="/login")
            generated.append({"blueprint": blueprint})
        return generated

    monkeypatch.setattr(oauth_bb, "generate_oauth_blueprints", generate)
    monkeypatch.setattr(oauth_bb, "_register_auto_redirect_hooks", lambda *_args: None)

    first = Flask("oauth-first")
    second = Flask("oauth-second")
    oauth_bb.init_oauth_blueprints(first)
    oauth_bb.init_oauth_blueprints(second)

    assert list(first.blueprints) == ["github", "google", "generic"]
    assert list(second.blueprints) == ["github", "google", "generic"]


@pytest.mark.unit
def test_unit_preload_propagates_real_package_import_failure():
    """A real-package preload error must abort instead of grading cps stubs."""
    conftest_path = str(Path(cps.constants.BASE_DIR) / "tests" / "unit" / "conftest.py")
    script = """
import builtins
import runpy
real_import = builtins.__import__
def failing_import(name, *args, **kwargs):
    if name == 'cps':
        raise RuntimeError('deliberate cps preload failure')
    return real_import(name, *args, **kwargs)
builtins.__import__ = failing_import
runpy.run_path(%r, run_name='preload_probe')
""" % conftest_path

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "deliberate cps preload failure" in result.stderr
