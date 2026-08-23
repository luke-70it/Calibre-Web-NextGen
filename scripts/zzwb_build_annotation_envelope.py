#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the never-merge ZZWB GET body from an offline Stage 0 DB snapshot."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple


PROBE_CONTENT_ID = "d83c9bfd-91e1-4bed-a1a6-9c50d15ae46c"
_CAPTURE_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "cps" / "services" / "kobo_annotation_capture.py"
)


class BuildError(RuntimeError):
    """The offline snapshot cannot produce a bounded exact probe envelope."""


class BuildResult(NamedTuple):
    payload: bytes
    annotation_count: int
    user_id: int
    book_id: int
    sha256: str


def _load_capture_module():
    """Load only the pure lexical projector, without importing the Flask app."""
    module_name = "_zzwb_kobo_annotation_capture"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, _CAPTURE_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise BuildError("could not load the Stage 0 lexical projector")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _read_only_connection(database: Path):
    path = database.expanduser().resolve()
    if not path.is_file():
        raise BuildError(f"database snapshot does not exist: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _probe_identity(connection, user_id):
    sql = (
        "SELECT DISTINCT user_id, book_id FROM kobo_annotation_book_state "
        "WHERE content_id = ?"
    )
    params = [PROBE_CONTENT_ID]
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(user_id)
    rows = connection.execute(sql, params).fetchall()
    if not rows:
        raise BuildError("offline snapshot has no probe book state")
    if len(rows) != 1:
        raise BuildError("probe book state is ambiguous; pass --user-id")
    return rows[0]["user_id"], rows[0]["book_id"]


def _materialization_rows(connection, user_id, book_id):
    return connection.execute(
        """
        SELECT a.annotation_id AS generic_annotation_id,
               m.raw_annotation_json,
               m.raw_location_json,
               m.payload_sha256,
               m.provenance
          FROM annotation AS a
          JOIN kobo_annotation_materialization AS m
            ON m.annotation_id = a.id
         WHERE a.user_id = ?
           AND a.book_id = ?
           AND COALESCE(a.hidden, 0) = 0
         ORDER BY a.annotation_id COLLATE BINARY ASC
        """,
        (user_id, book_id),
    ).fetchall()


def _blob(row, column):
    value = row[column]
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not isinstance(value, bytes):
        raise BuildError(f"{column} is not a SQLite BLOB")
    return value


def _exact_objects(rows):
    capture = _load_capture_module()
    objects = []
    seen_ids = set()
    for row in rows:
        raw_object = _blob(row, "raw_annotation_json")
        raw_location = _blob(row, "raw_location_json")
        if row["provenance"] not in {"kobo_patch", "kobo_cloud_seed"}:
            raise BuildError("materialization is not byte-captured Kobo provenance")
        actual_sha = hashlib.sha256(raw_object).hexdigest()
        if actual_sha != row["payload_sha256"]:
            raise BuildError("materialization payload SHA-256 mismatch")
        try:
            projected = capture.project_exact_materialization(raw_object, raw_location)
            parsed = json.loads(projected)
        except Exception as error:
            raise BuildError("materialization location/object invariant failed") from error
        annotation_id = parsed.get("id") if isinstance(parsed, dict) else None
        if annotation_id != row["generic_annotation_id"]:
            raise BuildError("materialization id differs from the generic annotation id")
        if annotation_id in seen_ids:
            raise BuildError("duplicate annotation id in probe materializations")
        seen_ids.add(annotation_id)
        objects.append(projected)
    return objects


def build_envelope(database, *, user_id=None, allow_empty=False):
    """Return the exact probe envelope built from one offline app.db snapshot."""
    try:
        with _read_only_connection(Path(database)) as connection:
            resolved_user_id, book_id = _probe_identity(connection, user_id)
            rows = _materialization_rows(connection, resolved_user_id, book_id)
    except sqlite3.Error as error:
        raise BuildError(f"offline snapshot query failed: {error}") from error
    if not rows and not allow_empty:
        raise BuildError(
            "probe has zero visible materializations; pass --allow-empty only intentionally"
        )
    objects = _exact_objects(rows)
    payload = (
        b'{"annotations":[' + b",".join(objects)
        + b'],"nextPageOffsetToken":null}'
    )
    return BuildResult(
        payload=payload,
        annotation_count=len(objects),
        user_id=resolved_user_id,
        book_id=book_id,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _write_atomic(output: Path, payload: bytes, *, force=False):
    output = output.expanduser().resolve()
    if output.exists() and not force:
        raise BuildError(f"output already exists (pass --force to replace): {output}")
    if not output.parent.is_dir():
        raise BuildError(f"output directory does not exist: {output.parent}")
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output.parent, prefix=f".{output.name}.", delete=False,
        ) as handle:
            temporary_name = handle.name
            os.chmod(temporary_name, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except Exception:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
        raise


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="offline app.db snapshot")
    parser.add_argument("--output", required=True, type=Path, help="payload.json destination")
    parser.add_argument("--user-id", type=int, help="disambiguate multiple probe owners")
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="explicitly permit the measured empty annotations envelope",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing output file")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        result = build_envelope(
            args.database, user_id=args.user_id, allow_empty=args.allow_empty,
        )
        _write_atomic(args.output, result.payload, force=args.force)
    except (BuildError, OSError) as error:
        print(f"ZZWB envelope build refused: {error}", file=sys.stderr)
        return 2
    print(
        "ZZWB envelope built "
        f"content_id={PROBE_CONTENT_ID} user_id={result.user_id} "
        f"book_id={result.book_id} annotations={result.annotation_count} "
        f"bytes={len(result.payload)} sha256={result.sha256} output={args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
