# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for fork issue #1614's state-directory contract."""

from __future__ import annotations

import ast
from collections import Counter
from importlib import import_module
from pathlib import Path

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
