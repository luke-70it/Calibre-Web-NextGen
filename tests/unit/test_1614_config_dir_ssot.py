# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for fork issue #1614's state-directory contract."""

from __future__ import annotations

import ast
from collections import Counter
from importlib import import_module
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.unit
REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolved_site_paths():
    state_paths = import_module("cps.state_paths")
    cwa_functions = import_module("cps.cwa_functions")
    auth = import_module("cps.api.auth")
    calibre_init = import_module("cps.calibre_init")
    duplicate_index = import_module("cps.duplicate_index")
    preview_cache = import_module("cps.services.cover_preview_cache")
    calibre_db_lock = import_module("cps.services.calibre_db_lock")
    calibre_user_plugins = import_module("cps.services.calibre_user_plugins")

    return {
        "update_notice": state_paths.update_notice_path(),
        "cwa_user_profiles": cwa_functions._state_paths().user_profiles_path(),
        "api_user_profiles": auth.state_paths.user_profiles_path(),
        "convert_library_log": state_paths.convert_library_log_path(),
        "epub_fixer_log": state_paths.epub_fixer_log_path(),
        "cover_enforcer_log": state_paths.cover_enforcer_log_path(),
        "ingest_batch_dirty": duplicate_index._ingest_batch_dirty_file(),
        "ingest_batch_active": duplicate_index._ingest_batch_active_file(),
        "preview_cache_root": str(preview_cache._default_cache_root()),
        "metadata_lock": calibre_db_lock._resolve_lock_path(None),
        "calibre_init_app_db": calibre_init._resolve_app_db_path(),
        "calibre_plugins_home": calibre_user_plugins.home_path(),
        "calibre_plugins_config": str(calibre_user_plugins.config_dir()),
        "calibre_plugins_dir": str(calibre_user_plugins.plugins_dir()),
    }


def test_state_paths_follow_monkeypatched_config_dir(tmp_path, monkeypatch):
    from cps import constants
    from cps.services import calibre_db_lock

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CWA_METADATA_LOCK_DIR", raising=False)

    expected = {
        "update_notice": str(tmp_path / "cwa_update_notice"),
        "cwa_user_profiles": str(tmp_path / "user_profiles.json"),
        "api_user_profiles": str(tmp_path / "user_profiles.json"),
        "convert_library_log": str(tmp_path / "convert-library.log"),
        "epub_fixer_log": str(tmp_path / "epub-fixer.log"),
        "cover_enforcer_log": str(tmp_path / "cover-enforcer.log"),
        "ingest_batch_dirty": str(tmp_path / "cwa_ingest_batch_dirty"),
        "ingest_batch_active": str(tmp_path / "cwa_ingest_batch_active"),
        "preview_cache_root": str(tmp_path / ".cwa-preview-cache"),
        "metadata_lock": str(tmp_path / ".cwa-metadata-write.lock"),
        "calibre_init_app_db": str(tmp_path / "app.db"),
        "calibre_plugins_home": str(tmp_path),
        "calibre_plugins_config": str(tmp_path / ".config" / "calibre"),
        "calibre_plugins_dir": str(tmp_path / ".config" / "calibre" / "plugins"),
    }
    assert _resolved_site_paths() == expected

    env_dir = tmp_path / "lock-env"
    explicit_dir = tmp_path / "lock-explicit"
    monkeypatch.setenv("CWA_METADATA_LOCK_DIR", str(env_dir))
    assert calibre_db_lock._resolve_lock_path(None) == str(
        env_dir / ".cwa-metadata-write.lock"
    )
    assert calibre_db_lock._resolve_lock_path(str(explicit_dir)) == str(
        explicit_dir / ".cwa-metadata-write.lock"
    )


def test_docker_paths_are_byte_identical_to_the_pre_1614_literals(monkeypatch):
    from cps import constants

    monkeypatch.setenv("CALIBRE_DBPATH", "/config")
    monkeypatch.setattr(constants, "CONFIG_DIR", "/config")
    monkeypatch.delenv("CWA_METADATA_LOCK_DIR", raising=False)

    assert _resolved_site_paths() == {
        "update_notice": "/config/cwa_update_notice",
        "cwa_user_profiles": "/config/user_profiles.json",
        "api_user_profiles": "/config/user_profiles.json",
        "convert_library_log": "/config/convert-library.log",
        "epub_fixer_log": "/config/epub-fixer.log",
        "cover_enforcer_log": "/config/cover-enforcer.log",
        "ingest_batch_dirty": "/config/cwa_ingest_batch_dirty",
        "ingest_batch_active": "/config/cwa_ingest_batch_active",
        "preview_cache_root": "/config/.cwa-preview-cache",
        "metadata_lock": "/config/.cwa-metadata-write.lock",
        "calibre_init_app_db": "/config/app.db",
        "calibre_plugins_home": "/config",
        "calibre_plugins_config": "/config/.config/calibre",
        "calibre_plugins_dir": "/config/.config/calibre/plugins",
    }


def test_frozen_docker_paths_ignore_constants_parent_rewrite():
    code = """
import json
import sys
sys.frozen = True
from cps import constants, state_paths
print(json.dumps({
    "constants_config_dir": constants.CONFIG_DIR,
    "config_dir": state_paths.config_dir(),
    "update_notice": state_paths.update_notice_path(),
    "user_profiles": state_paths.user_profiles_path(),
    "ingest_status": state_paths.ingest_status_path(),
    "ingest_retry_queue": state_paths.ingest_retry_queue_path(),
    "convert_library_log": state_paths.convert_library_log_path(),
    "epub_fixer_log": state_paths.epub_fixer_log_path(),
    "cover_enforcer_log": state_paths.cover_enforcer_log_path(),
    "ingest_batch_dirty": state_paths.ingest_batch_dirty_path(),
    "ingest_batch_active": state_paths.ingest_batch_active_path(),
    "preview_cache_root": str(state_paths.preview_cache_root()),
    "app_db": state_paths.app_db_path(),
    "duplicate_resolution": state_paths.duplicate_resolution_dir("20260816", "abcd1234"),
    "restore_backup": state_paths.restore_backup_dir("20260816"),
}, sort_keys=True))
"""
    env = os.environ.copy()
    env["CALIBRE_DBPATH"] = "/config"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    actual = json.loads(result.stdout.strip().splitlines()[-1])

    assert actual == {
        "constants_config_dir": "/",
        "config_dir": "/config",
        "update_notice": "/config/cwa_update_notice",
        "user_profiles": "/config/user_profiles.json",
        "ingest_status": "/config/cwa_ingest_status",
        "ingest_retry_queue": "/config/cwa_ingest_retry_queue",
        "convert_library_log": "/config/convert-library.log",
        "epub_fixer_log": "/config/epub-fixer.log",
        "cover_enforcer_log": "/config/cover-enforcer.log",
        "ingest_batch_dirty": "/config/cwa_ingest_batch_dirty",
        "ingest_batch_active": "/config/cwa_ingest_batch_active",
        "preview_cache_root": "/config/.cwa-preview-cache",
        "app_db": "/config/app.db",
        "duplicate_resolution": "/config/processed_books/duplicate_resolutions/20260816_group_abcd1234",
        "restore_backup": "/config/backup/restore_20260816",
    }


def test_db_valued_calibre_dbpath_resolves_state_beside_app_db(tmp_path, monkeypatch):
    from cps import state_paths
    from cps import calibre_init

    configured_db = tmp_path / "custom.db"
    monkeypatch.setenv("CALIBRE_DBPATH", str(configured_db))
    monkeypatch.delenv("CWA_METADATA_LOCK_DIR", raising=False)

    resolved = _resolved_site_paths()
    app_db = Path(calibre_init._resolve_app_db_path())
    assert state_paths.config_dir() == str(tmp_path)
    assert app_db == tmp_path / "app.db"
    for key in (
        "update_notice",
        "cwa_user_profiles",
        "api_user_profiles",
        "convert_library_log",
        "epub_fixer_log",
        "cover_enforcer_log",
        "ingest_batch_dirty",
        "ingest_batch_active",
        "preview_cache_root",
        "metadata_lock",
    ):
        assert Path(resolved[key]).parent == app_db.parent, key
    assert resolved["calibre_plugins_home"] == str(app_db.parent)


def test_cover_preview_cache_loads_standalone(tmp_path):
    module_path = REPO_ROOT / "cps" / "services" / "cover_preview_cache.py"
    spec = importlib.util.spec_from_file_location("_cover_preview_cache_standalone", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.CACHE_ROOT is None
    module.CACHE_ROOT = tmp_path
    assert module.cache_path("abcdef") == tmp_path / "ab" / "cdef.jpg"


@pytest.mark.parametrize("configured", ["", "/"])
def test_debug_redaction_rejects_overbroad_config_candidates(configured, monkeypatch):
    from cps import debug_info

    monkeypatch.setenv("CALIBRE_DBPATH", configured)
    pattern = debug_info._config_path_pattern()

    assert pattern.sub("<config>/", "/var/log/cwng/output.log") == "/var/log/cwng/output.log"
    assert pattern.sub("<config>/", "/config/private.log") == "<config>/private.log"


def test_debug_redaction_prefers_nested_config_path(monkeypatch):
    from cps import debug_info

    monkeypatch.setenv("CALIBRE_DBPATH", "/config/private-install")
    pattern = debug_info._config_path_pattern()

    assert pattern.sub("<config>/", "/config/private-install/secret.log") == "<config>/secret.log"


def test_debug_redaction_escapes_regex_metacharacters(monkeypatch):
    from cps import debug_info

    configured = "/srv/state[1](prod)+?"
    monkeypatch.setenv("CALIBRE_DBPATH", configured)
    pattern = debug_info._config_path_pattern()

    assert pattern.sub("<config>/", configured + "/secret.log") == "<config>/secret.log"
    assert pattern.search("/srv/state1prod/secret.log") is None


def test_debug_redaction_covers_raw_and_normalized_config_paths(tmp_path, monkeypatch):
    from cps import debug_info

    configured = str(tmp_path / "one" / ".." / "state")
    normalized = os.path.normpath(configured)
    monkeypatch.setenv("CALIBRE_DBPATH", configured)
    pattern = debug_info._config_path_pattern()

    assert pattern.sub("<config>/", configured + "/raw.log") == "<config>/raw.log"
    assert pattern.sub("<config>/", normalized + "/normalized.log") == "<config>/normalized.log"


def _docstring_constant_ids(tree):
    ids = set()
    scopes = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, scopes) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def test_no_bare_container_config_literals_outside_deliberate_survivors():
    # These literals are intentionally broader than CONFIG_DIR: updater must
    # continue recognizing the image's mount name, while debug bundles must
    # always redact that mount even when the configured state dir differs.
    allowed = Counter({
        ("cps/updater.py", "/config"): 1,
        ("cps/updater.py", "/config/"): 1,
        ("cps/debug_info.py", "/config/"): 1,
    })
    found = Counter()
    locations = []

    for source_path in sorted((REPO_ROOT / "cps").rglob("*.py")):
        relative = source_path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative)
        docstrings = _docstring_constant_ids(tree)
        for node in ast.walk(tree):
            if id(node) in docstrings or not isinstance(node, ast.Constant):
                continue
            value = node.value
            if isinstance(value, str) and (value == "/config" or value.startswith("/config/")):
                found[(relative, value)] += 1
                locations.append(f"{relative}:{node.lineno}: {value!r}")

    assert found == allowed, (
        "bare container-only state paths changed; route application state "
        "through cps.state_paths (only the documented mount/redaction "
        f"survivors are allowed). Found:\n" + "\n".join(locations)
    )
