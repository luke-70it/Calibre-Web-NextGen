#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a captured or one-highlight never-merge ZZWB annotation GET body."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


PROBE_CONTENT_ID = "053742ff-9094-43b2-8511-c0763c90ffab"
PROBE_BOOK_ID = 540
MAX_SERVER_HIGHLIGHT_SPEC_BYTES = 64 * 1024
MAX_RECOVERY_EXPORT_BYTES = 8 * 1024 * 1024
KOBO_HIGHLIGHT_COLORS = {
    "#F6F3B3", "#E8AFCF", "#B2E1E8", "#C6E09E", "#A0A0A0",
}
KOBO_COLOR_NAMES = {
    "yellow": "#F6F3B3", "pink": "#E8AFCF", "blue": "#B2E1E8",
    "green": "#C6E09E", "grey": "#A0A0A0", "gray": "#A0A0A0",
}
SERVER_HIGHLIGHT_KEYS = {
    "attachments", "clientLastModifiedUtc", "context", "highlightColor",
    "highlightedText", "id", "location", "type",
}
SERVER_HIGHLIGHT_SPAN_KEYS = {
    "chapterFilename", "chapterProgress", "endChar", "endPath",
    "startChar", "startPath",
}
RECOVERY_REQUIRED_COLUMNS = {
    "annotation_id", "annotation_type", "book_id", "chapter_progress",
    "client_modified_at", "content_id", "context_string", "end_container_path",
    "end_offset", "hidden", "highlight_color", "highlighted_text", "note_text",
    "source", "start_container_path", "start_offset",
}
_CAPTURE_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "cps" / "services" / "kobo_annotation_capture.py"
)


class BuildError(RuntimeError):
    """The offline snapshot cannot produce a bounded exact probe envelope."""


class BuildResult(NamedTuple):
    payload: bytes
    annotation_count: int
    user_id: int | None
    book_id: int | None
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
    book_id = rows[0]["book_id"]
    if book_id != PROBE_BOOK_ID:
        raise BuildError(
            f"probe UUID resolves to book {book_id}, expected {PROBE_BOOK_ID}"
        )
    return rows[0]["user_id"], book_id


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


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BuildError(f"server highlight spec has duplicate key: {key}")
        result[key] = value
    return result


def _require_string(mapping, key, *, allow_empty=False):
    value = mapping.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise BuildError(f"server highlight {key} must be a non-empty string")
    return value


def _validate_server_highlight(annotation):
    """Validate the exact successful Kobo GET highlight shape used by Cycle B."""
    if not isinstance(annotation, dict):
        raise BuildError("server highlight spec must contain one JSON object")
    if set(annotation) != SERVER_HIGHLIGHT_KEYS:
        missing = sorted(SERVER_HIGHLIGHT_KEYS - set(annotation))
        extra = sorted(set(annotation) - SERVER_HIGHLIGHT_KEYS)
        raise BuildError(
            f"server highlight fields differ from captured GET shape; "
            f"missing={missing} extra={extra}"
        )
    if annotation["attachments"] != {}:
        raise BuildError("server highlight attachments must be the observed empty object")
    if annotation["type"] != "highlight":
        raise BuildError("server highlight type must be exactly 'highlight'")
    for key in ("id", "clientLastModifiedUtc", "context", "highlightedText"):
        _require_string(annotation, key, allow_empty=(key == "context"))
    try:
        annotation_id = uuid.UUID(annotation["id"])
    except (ValueError, AttributeError) as error:
        raise BuildError("server highlight id must be a UUID") from error
    if str(annotation_id) != annotation["id"]:
        raise BuildError("server highlight id must use canonical lower-case UUID spelling")
    raw_modified = annotation["clientLastModifiedUtc"]
    try:
        modified = datetime.fromisoformat(raw_modified[:-1] + "+00:00") \
            if raw_modified.endswith("Z") else None
    except (ValueError, OverflowError):
        modified = None
    if modified is None or modified.utcoffset() != timezone.utc.utcoffset(modified):
        raise BuildError("server highlight clientLastModifiedUtc must be UTC RFC 3339 ending Z")
    if annotation["highlightColor"] not in KOBO_HIGHLIGHT_COLORS:
        raise BuildError("server highlight color is outside Kobo's measured five-color palette")

    location = annotation.get("location")
    if not isinstance(location, dict) or set(location) != {"span"}:
        raise BuildError("server highlight location must be exactly a span object")
    span = location.get("span")
    if not isinstance(span, dict) or set(span) != SERVER_HIGHLIGHT_SPAN_KEYS:
        missing = sorted(SERVER_HIGHLIGHT_SPAN_KEYS - set(span or {})) \
            if isinstance(span, dict) else sorted(SERVER_HIGHLIGHT_SPAN_KEYS)
        extra = sorted(set(span) - SERVER_HIGHLIGHT_SPAN_KEYS) \
            if isinstance(span, dict) else []
        raise BuildError(
            f"server highlight span differs from captured GET shape; "
            f"missing={missing} extra={extra}"
        )
    for key in ("chapterFilename", "startPath", "endPath"):
        _require_string(span, key)
    progress = span["chapterProgress"]
    if isinstance(progress, bool) or not isinstance(progress, (int, float)) \
            or not 0 <= progress <= 1:
        raise BuildError("server highlight chapterProgress must be between 0 and 1")
    for key in ("startChar", "endChar"):
        value = span[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BuildError(f"server highlight {key} must be a non-negative integer")
    if span["startPath"] == span["endPath"] \
            and span["startChar"] > span["endChar"]:
        raise BuildError("server highlight character range is reversed")


def build_server_highlight_envelope(specification):
    """Build a one-row authored envelope from a reviewed Kobo-shape JSON spec."""
    specification = Path(specification).expanduser().resolve()
    if not specification.is_file():
        raise BuildError(f"server highlight spec does not exist: {specification}")
    raw = specification.read_bytes()
    if len(raw) > MAX_SERVER_HIGHLIGHT_SPEC_BYTES:
        raise BuildError("server highlight spec exceeds 64 KiB")
    try:
        annotation = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise BuildError("server highlight spec is not one valid UTF-8 JSON object") from error
    _validate_server_highlight(annotation)
    raw_annotation = json.dumps(
        annotation, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    payload = (
        b'{"annotations":[' + raw_annotation
        + b'],"nextPageOffsetToken":null}'
    )
    return BuildResult(
        payload=payload,
        annotation_count=1,
        user_id=None,
        book_id=None,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _reject_recovery_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BuildError(f"annotation export has duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_uuid(value, *, label):
    if not isinstance(value, str):
        raise BuildError(f"{label} must be a canonical lower-case UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise BuildError(f"{label} must be a canonical lower-case UUID") from error
    if str(parsed) != value:
        raise BuildError(f"{label} must be a canonical lower-case UUID")
    return value


def _recovery_string(row, key, row_number, *, allow_empty=False):
    value = row.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise BuildError(f"recovery row {row_number} {key} must be {qualifier}")
    if len(value.encode("utf-8")) > 64 * 1024:
        raise BuildError(f"recovery row {row_number} {key} exceeds 64 KiB")
    return value


def _recovery_timestamp(value, row_number):
    if not isinstance(value, str) or not value.strip():
        raise BuildError(
            f"recovery row {row_number} client_modified_at must be a stored UTC timestamp"
        )
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except (ValueError, OverflowError) as error:
        raise BuildError(
            f"recovery row {row_number} client_modified_at is not ISO 8601"
        ) from error
    # Annotation.client_modified_at uses the application's naive-UTC storage
    # convention; an offset-bearing export is normalized to the same instant.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _recovery_chapter_filename(value, content_id, row_number):
    value = _recovery_string(
        {"content_id": value}, "content_id", row_number,
    )
    prefix = f"{content_id}!!"
    if not value.startswith(prefix):
        raise BuildError(
            f"recovery row {row_number} content_id does not belong to the target book"
        )
    chapter = value[len(prefix):]
    if (
        not chapter
        or len(chapter.encode("utf-8")) > 1536
        or chapter.startswith("/")
        or "\\" in chapter
        or "!!" in chapter
        or any(ord(character) < 32 or ord(character) == 127 for character in chapter)
        or any(part in {"", ".", ".."} for part in chapter.split("/"))
    ):
        raise BuildError(f"recovery row {row_number} has an unsafe chapter filename")
    return chapter


def _recovery_span(row, content_id, row_number):
    progress = row.get("chapter_progress")
    if (
        isinstance(progress, bool)
        or not isinstance(progress, (int, float))
        or not 0 <= progress <= 1
    ):
        raise BuildError(
            f"recovery row {row_number} chapter_progress must be between 0 and 1"
        )
    offsets = {}
    for key in ("start_offset", "end_offset"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BuildError(
                f"recovery row {row_number} {key} must be a non-negative integer"
            )
        offsets[key] = value
    start_path = _recovery_string(row, "start_container_path", row_number)
    end_path = _recovery_string(row, "end_container_path", row_number)
    if start_path == end_path and offsets["start_offset"] > offsets["end_offset"]:
        raise BuildError(f"recovery row {row_number} has a reversed character range")
    return {
        "chapterFilename": _recovery_chapter_filename(
            row.get("content_id"), content_id, row_number,
        ),
        "chapterProgress": progress,
        "endChar": offsets["end_offset"],
        "endPath": end_path,
        "startChar": offsets["start_offset"],
        "startPath": start_path,
    }


def _recovery_highlight_color(value, row_number):
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"recovery row {row_number} highlight_color is missing")
    token = value.strip()
    wire = KOBO_COLOR_NAMES.get(token.casefold(), token.upper())
    if wire not in KOBO_HIGHLIGHT_COLORS:
        raise BuildError(
            f"recovery row {row_number} highlight_color is outside Kobo's wire palette"
        )
    return wire


def _recovery_annotation(row, *, content_id, book_id, row_number):
    if not isinstance(row, dict):
        raise BuildError(f"recovery row {row_number} is not a JSON object")
    missing = sorted(RECOVERY_REQUIRED_COLUMNS - set(row))
    if missing:
        raise BuildError(f"recovery row {row_number} is missing columns: {missing}")
    if isinstance(row["book_id"], bool) or row["book_id"] != book_id:
        raise BuildError(f"recovery row {row_number} does not belong to book {book_id}")
    if row["source"] != "kobo":
        raise BuildError(f"recovery row {row_number} source must be exactly 'kobo'")
    hidden = row["hidden"]
    if not (hidden is False or (type(hidden) is int and hidden == 0)):
        raise BuildError(f"recovery row {row_number} is hidden or has an invalid hidden flag")
    annotation_id = _recovery_string(row, "annotation_id", row_number)
    if len(annotation_id.encode("utf-8")) > 256:
        raise BuildError(f"recovery row {row_number} annotation_id exceeds 256 bytes")
    annotation_type = row["annotation_type"]
    if annotation_type not in {"highlight", "dogear"}:
        raise BuildError(
            f"recovery row {row_number} type is not a proven Kobo recovery shape"
        )
    if row["note_text"] not in (None, ""):
        raise BuildError(
            f"recovery row {row_number} carries note_text without a proven serializer"
        )
    highlighted_text = _recovery_string(
        row, "highlighted_text", row_number, allow_empty=True,
    )
    context = _recovery_string(row, "context_string", row_number, allow_empty=True)
    common = {
        "clientLastModifiedUtc": _recovery_timestamp(
            row["client_modified_at"], row_number,
        ),
        "context": context,
        # This is the original BookmarkID captured from the device, never a
        # generated replacement id.
        "highlightedText": highlighted_text,
        "id": annotation_id,
        "location": {"span": _recovery_span(row, content_id, row_number)},
        "type": annotation_type,
    }
    if annotation_type == "dogear":
        if highlighted_text != "":
            raise BuildError(
                f"recovery row {row_number} dogear highlighted_text must be empty"
            )
        if row["highlight_color"] is not None:
            raise BuildError(f"recovery row {row_number} dogear must be colorless")
        chapter_title = _recovery_string(row, "chapter_title", row_number)
        common["location"]["span"]["chapterTitle"] = chapter_title
        # Tonight's captured device create PATCH carried highlightedText=""
        # plus chapterTitle, and no attachments/highlightColor members.
        return common
    if not highlighted_text:
        raise BuildError(f"recovery row {row_number} highlight text is empty")
    return {
        "attachments": {},
        "clientLastModifiedUtc": common["clientLastModifiedUtc"],
        "context": common["context"],
        "highlightColor": _recovery_highlight_color(
            row["highlight_color"], row_number,
        ),
        "highlightedText": common["highlightedText"],
        "id": common["id"],
        "location": common["location"],
        "type": common["type"],
    }


def build_recovery_envelope(
    export, *, content_id, book_id, expected_count,
):
    """Build a complete Kobo envelope from a reviewed annotation-table export."""
    content_id = _canonical_uuid(content_id, label="recovery content id")
    if isinstance(book_id, bool) or not isinstance(book_id, int) or book_id <= 0:
        raise BuildError("recovery book id must be a positive integer")
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count <= 0
    ):
        raise BuildError("recovery expected count must be a positive integer")
    export = Path(export).expanduser().resolve()
    if not export.is_file():
        raise BuildError(f"annotation export does not exist: {export}")
    raw = export.read_bytes()
    if len(raw) > MAX_RECOVERY_EXPORT_BYTES:
        raise BuildError("annotation export exceeds 8 MiB")
    try:
        rows = json.loads(raw, object_pairs_hook=_reject_recovery_duplicate_keys)
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise BuildError("annotation export is not valid duplicate-free UTF-8 JSON") from error
    if not isinstance(rows, list):
        raise BuildError("annotation export must be a JSON array of app.db rows")
    if len(rows) != expected_count:
        raise BuildError(
            f"annotation export has {len(rows)} rows, expected {expected_count}"
        )
    annotations = [
        _recovery_annotation(
            row, content_id=content_id, book_id=book_id, row_number=index,
        )
        for index, row in enumerate(rows, start=1)
    ]
    annotation_ids = [annotation["id"] for annotation in annotations]
    if len(set(annotation_ids)) != len(annotation_ids):
        raise BuildError("annotation export contains duplicate annotation_id values")
    annotations.sort(key=lambda annotation: annotation["id"])
    payload = json.dumps(
        {"annotations": annotations, "nextPageOffsetToken": None},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_RECOVERY_EXPORT_BYTES:
        raise BuildError("recovery payload exceeds the rig's 8 MiB stage limit")
    return BuildResult(
        payload=payload,
        annotation_count=len(annotations),
        user_id=None,
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path, help="offline app.db snapshot")
    source.add_argument(
        "--server-highlight", type=Path,
        help="reviewed one-highlight JSON spec in the captured Kobo GET shape",
    )
    source.add_argument(
        "--annotation-export", type=Path,
        help="reviewed JSON export of visible app.db annotation rows",
    )
    parser.add_argument("--output", required=True, type=Path, help="payload.json destination")
    parser.add_argument("--user-id", type=int, help="disambiguate multiple probe owners")
    parser.add_argument("--content-id", help="recovery target ContentId UUID")
    parser.add_argument("--book-id", type=int, help="recovery target Calibre book id")
    parser.add_argument(
        "--expected-count", type=int,
        help="required recovery row count; refuses incomplete exports",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="explicitly permit the measured empty annotations envelope",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing output file")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        recovery_arguments = (args.content_id, args.book_id, args.expected_count)
        if args.annotation_export is not None:
            if args.user_id is not None or args.allow_empty:
                raise BuildError(
                    "--user-id/--allow-empty do not apply to recovery exports"
                )
            if any(value is None for value in recovery_arguments):
                raise BuildError(
                    "--annotation-export requires --content-id, --book-id, and --expected-count"
                )
            result = build_recovery_envelope(
                args.annotation_export,
                content_id=args.content_id,
                book_id=args.book_id,
                expected_count=args.expected_count,
            )
            built_content_id = args.content_id
        elif args.server_highlight is not None:
            if args.user_id is not None or args.allow_empty:
                raise BuildError(
                    "--user-id/--allow-empty apply only to --database builds"
                )
            if any(value is not None for value in recovery_arguments):
                raise BuildError(
                    "--content-id/--book-id/--expected-count apply only to recovery exports"
                )
            result = build_server_highlight_envelope(args.server_highlight)
            built_content_id = PROBE_CONTENT_ID
        else:
            if any(value is not None for value in recovery_arguments):
                raise BuildError(
                    "--content-id/--book-id/--expected-count apply only to recovery exports"
                )
            result = build_envelope(
                args.database, user_id=args.user_id, allow_empty=args.allow_empty,
            )
            built_content_id = PROBE_CONTENT_ID
        _write_atomic(args.output, result.payload, force=args.force)
    except (BuildError, OSError) as error:
        print(f"ZZWB envelope build refused: {error}", file=sys.stderr)
        return 2
    print(
        "ZZWB envelope built "
        f"content_id={built_content_id} user_id={result.user_id} "
        f"book_id={result.book_id} annotations={result.annotation_count} "
        f"bytes={len(result.payload)} sha256={result.sha256} output={args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
