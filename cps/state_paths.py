# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Paths for application state stored beside ``app.db``."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> str:
    """Return the configured state directory without importing Flask eagerly."""
    from .constants import CONFIG_DIR

    return CONFIG_DIR


def state_path(*parts: str) -> str:
    return os.path.join(config_dir(), *parts)


def update_notice_path() -> str:
    return state_path("cwa_update_notice")


def user_profiles_path() -> str:
    return state_path("user_profiles.json")


def ingest_status_path() -> str:
    return state_path("cwa_ingest_status")


def ingest_retry_queue_path() -> str:
    return state_path("cwa_ingest_retry_queue")


def convert_library_log_path() -> str:
    return state_path("convert-library.log")


def epub_fixer_log_path() -> str:
    return state_path("epub-fixer.log")


def cover_enforcer_log_path() -> str:
    return state_path("cover-enforcer.log")


def ingest_batch_dirty_path() -> str:
    return state_path("cwa_ingest_batch_dirty")


def ingest_batch_active_path() -> str:
    return state_path("cwa_ingest_batch_active")


def preview_cache_root() -> Path:
    return Path(state_path(".cwa-preview-cache"))


def app_db_path() -> str:
    return state_path("app.db")


def duplicate_resolution_dir(timestamp: str, group_hash_prefix: str) -> str:
    return state_path(
        "processed_books",
        "duplicate_resolutions",
        f"{timestamp}_group_{group_hash_prefix}",
    )


def restore_backup_dir(timestamp: str) -> str:
    return state_path("backup", f"restore_{timestamp}")
