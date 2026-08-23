# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, durable recovery spool for raw Kobo annotation PATCH bodies.

Unlike the opt-in exchange observer, this is an always-on data-integrity
primitive.  The route fsyncs an exact body here before JSON parsing or local
dispatch.  Successful records remain briefly too: the dispatcher historically
could continue after a member-level commit failure, so a returned call is not
strong enough evidence that every delta landed.

The spool stores no request headers or credentials.  It is a local recovery
artifact, excluded from annotation backups and support bundles, and pruned by
age, count, and compressed bytes.
"""

from __future__ import annotations

import base64
import fcntl
import gzip
import hashlib
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import constants


log = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 512
MAX_AGE_SECONDS = 14 * 24 * 60 * 60

_PROCESS_LOCK = threading.Lock()
_VALID_OUTCOMES = {"staged", "dispatch_completed", "dispatch_exception"}


def _spool_root() -> Path:
    return (
        Path(constants.CONFIG_DIR)
        / ".cwng-private-observability"
        / "kobo-patch-spool"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def _body_within_bound(raw_body) -> bool:
    try:
        return len(raw_body) <= MAX_BODY_BYTES
    except TypeError:
        return False


def is_replay_candidate(status) -> bool:
    return status in {"staged", "dispatch_exception"}


def stage_patch(*, raw_body, entitlement_id, user_id, origin_device_id):
    """Atomically fsync an exact PATCH body and return its outcome ticket."""
    if not _body_within_bound(raw_body):
        log.error(
            "Kobo PATCH recovery spool skipped body outside bound bytes=%s",
            len(raw_body) if isinstance(raw_body, (bytes, bytearray)) else None,
        )
        return None
    try:
        raw_body = bytes(raw_body)
        spool_id = secrets.token_hex(16)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "schema_version": 1,
            "spool_id": spool_id,
            "received_at": now,
            "entitlement_id": str(entitlement_id),
            "user_id": user_id,
            "origin_device_id": origin_device_id,
            "body_encoding": "base64",
            "body_length": len(raw_body),
            "body_sha256": sha256_bytes(raw_body),
            "body_base64": base64.b64encode(raw_body).decode("ascii"),
            "dispatch_status": "staged",
            "dispatch_updated_at": now,
        }
        compressed = _compress(record)
        path = _write_new_record(spool_id, compressed)
        log.info(
            "Kobo PATCH recovery body staged spool_id=%s user_id=%s bytes=%s",
            spool_id, user_id, len(raw_body),
        )
        return PatchSpoolTicket(spool_id=spool_id, path=path)
    except Exception:
        log.error(
            "Kobo PATCH recovery spool failed user_id=%s bytes=%s",
            user_id,
            len(raw_body) if isinstance(raw_body, (bytes, bytearray)) else None,
            exc_info=True,
        )
        return None


class PatchSpoolTicket:
    def __init__(self, *, spool_id, path):
        self.spool_id = spool_id
        self.path = Path(path)

    def mark_dispatch_outcome(self, status) -> bool:
        if status not in _VALID_OUTCOMES - {"staged"}:
            raise ValueError("invalid Kobo PATCH dispatch outcome")
        try:
            with _PROCESS_LOCK:
                root = _spool_root()
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(root, 0o700)
                with _locked_root(root):
                    record = _load_disk_record(self.path)
                    record["dispatch_status"] = status
                    record["dispatch_updated_at"] = datetime.now(timezone.utc).isoformat()
                    _replace_record_locked(self.path, _compress(record))
            return True
        except Exception:
            log.error(
                "Kobo PATCH recovery outcome update failed spool_id=%s status=%s",
                self.spool_id, status, exc_info=True,
            )
            return False


class _RootLock:
    def __init__(self, root):
        self.root = root
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.root / ".spool.lock", os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(self.fd, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _locked_root(root):
    return _RootLock(root)


def _compress(record) -> bytes:
    serialized = json.dumps(
        record, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return gzip.compress(serialized, compresslevel=6, mtime=0)


def _record_paths(root) -> list[Path]:
    paths = list(Path(root).glob("patch-*.json.gz"))
    paths.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
    return paths


def _count_requires_prune(count, *, incoming):
    return count >= MAX_FILES if incoming else count > MAX_FILES


def _prune_locked(root, *, incoming_bytes):
    now = time.time()
    paths = _record_paths(root)
    for path in list(paths):
        try:
            if now - path.stat().st_mtime > MAX_AGE_SECONDS:
                path.unlink()
                paths.remove(path)
        except FileNotFoundError:
            paths.remove(path)

    total = sum(path.stat().st_size for path in paths if path.exists())
    while paths and (
        _count_requires_prune(len(paths), incoming=bool(incoming_bytes))
        or total + incoming_bytes > MAX_TOTAL_BYTES
    ):
        oldest = paths.pop(0)
        try:
            size = oldest.stat().st_size
            oldest.unlink()
            total -= size
        except FileNotFoundError:
            pass
    if incoming_bytes and (
        MAX_FILES < 1 or total + incoming_bytes > MAX_TOTAL_BYTES
    ):
        raise ValueError("Kobo PATCH spool retention leaves no room")


def _write_new_record(spool_id, compressed):
    if len(compressed) > MAX_TOTAL_BYTES:
        raise ValueError("one Kobo PATCH spool record exceeds total bound")
    root = _spool_root()
    with _PROCESS_LOCK:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        with _locked_root(root):
            _prune_locked(root, incoming_bytes=len(compressed))
            path = root / f"patch-{time.time_ns():020d}-{spool_id}.json.gz"
            _replace_record_locked(path, compressed)
            _prune_locked(root, incoming_bytes=0)
            return path


def _replace_record_locked(path, compressed):
    path = Path(path)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=".patch-", suffix=".tmp", dir=path.parent,
    )
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "wb") as stream:
            temp_fd = None
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _load_disk_record(path):
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def load_spooled_patch(path):
    """Load routing metadata plus the exact raw body for controlled replay."""
    record = _load_disk_record(path)
    encoded = record.pop("body_base64")
    body = base64.b64decode(encoded, validate=True)
    if len(body) != record["body_length"] or sha256_bytes(body) != record["body_sha256"]:
        raise ValueError("Kobo PATCH spool body integrity check failed")
    record["body"] = body
    return record


def iter_replay_candidates():
    root = _spool_root()
    if not root.exists():
        return
    for path in _record_paths(root):
        try:
            if is_replay_candidate(_load_disk_record(path).get("dispatch_status")):
                yield path
        except Exception:
            log.error("Unreadable Kobo PATCH recovery record path=%s", path.name, exc_info=True)
