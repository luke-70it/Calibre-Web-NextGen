# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durably seed CWNG's owned-book Kobo annotation authority.

The device always receives Kobo's proxied response for the seeding request.
This module records that response, reconciles the complete upstream set into
the generic annotation store, and promotes only after the replacement set is
provably complete and small enough for CWNG's single-page local renderer.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from cps import ub
from cps.services import kobo_annotation_stage0


LOCAL_PAGE_CAPACITY = 100
_SAFE_FAILURE_REASONS = frozenset({
    "seed_capture_requires_pagination",
    "seed_content_id_conflict",
    "seed_duplicate_annotation_id",
    "seed_local_count_below_capture",
    "seed_local_set_requires_pagination",
    "seed_response_invalid",
})


def _now():
    return datetime.now(timezone.utc)


def _normalized_book_uuid(book):
    value = getattr(book, "uuid", None)
    if not isinstance(value, str):
        return None
    value = value.strip().strip("{}").strip().casefold()
    return value if value and len(value) <= 64 else None


def _state_for_book(user_id, book_id):
    return (
        ub.session.query(ub.KoboAnnotationBookState)
        .filter(
            ub.KoboAnnotationBookState.user_id == user_id,
            ub.KoboAnnotationBookState.book_id == book_id,
        )
        .first()
    )


def _requesting_device(user_id, device_id):
    if not isinstance(device_id, int) or isinstance(device_id, bool):
        return None
    return (
        ub.session.query(ub.Device)
        .filter(
            ub.Device.id == device_id,
            ub.Device.user_id == user_id,
            ub.Device.kind == "kobo",
        )
        .first()
    )


def _accepted_capture(book_state_id, device_id):
    return (
        ub.session.query(ub.KoboAnnotationSeedCapture.id)
        .filter(
            ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
            ub.KoboAnnotationSeedCapture.device_id == device_id,
            ub.KoboAnnotationSeedCapture.result == "accepted",
            ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
        )
        .first()
    )


def _pending_capture(book_state_id, device_id):
    return (
        ub.session.query(ub.KoboAnnotationSeedCapture)
        .filter(
            ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
            ub.KoboAnnotationSeedCapture.device_id == device_id,
            ub.KoboAnnotationSeedCapture.result == "pending",
        )
        .order_by(
            ub.KoboAnnotationSeedCapture.started_at.desc(),
            ub.KoboAnnotationSeedCapture.id.desc(),
        )
        .first()
    )


def _expected_offset(capture):
    page = (
        ub.session.query(ub.KoboAnnotationSeedCapturePage)
        .filter(ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture.id)
        .order_by(ub.KoboAnnotationSeedCapturePage.page_number.desc())
        .first()
    )
    return None if page is None else page.next_offset_token


def _seeding_gates_allow(settings, user):
    try:
        return (
            not kobo_annotation_stage0.emergency_override_disables()
            and kobo_annotation_stage0.schema_capable(ub.session.get_bind())
            and bool(getattr(settings, "config_kobo_two_way_annotation_sync", False))
            and bool(getattr(user, "kobo_two_way_annotation_sync", False))
        )
    except Exception:
        return False


def begin_or_resume_capture(
    *, settings, user, book, device_id, request_offset_token,
    device_etag=None, log,
):
    """Return a pending capture id for this proxied GET, or ``None``.

    A first seed transitions an unseeded book to ``seeding``. Once one device
    has promoted the user-wide set, another active Kobo can capture against the
    same authoritative state without disrupting devices that are already local.
    """
    user_id = getattr(user, "id", None)
    book_id = getattr(book, "id", None)
    if not _seeding_gates_allow(settings, user):
        return None
    if _requesting_device(user_id, device_id) is None:
        return None

    try:
        state = _state_for_book(user_id, book_id)
        if state is None:
            normalized_content_id = _normalized_book_uuid(book)
            if normalized_content_id is None:
                return None
            conflict = (
                ub.session.query(ub.KoboAnnotationBookState.id)
                .filter(
                    ub.KoboAnnotationBookState.user_id == user_id,
                    ub.KoboAnnotationBookState.content_id == normalized_content_id,
                    ub.KoboAnnotationBookState.book_id != book_id,
                )
                .first()
            )
            if conflict is not None:
                return None
            candidate = ub.KoboAnnotationBookState(
                user_id=user_id,
                book_id=book_id,
                content_id=normalized_content_id,
                authority_status="unseeded",
                authority_revision=0,
                generation_id=str(uuid.uuid4()),
                ever_authoritative=False,
                opaque_content_status="unknown",
            )
            try:
                with ub.begin_contained_nested(ub.session):
                    ub.session.add(candidate)
                    ub.session.flush()
            except IntegrityError:
                state = _state_for_book(user_id, book_id)
                if state is None:
                    return None
            else:
                state = candidate

        pending = _pending_capture(state.id, device_id)
        if pending is not None:
            if _expected_offset(pending) != request_offset_token:
                return None
            return pending.id

        # Never start a new capture in the middle of an upstream page chain.
        if request_offset_token is not None:
            return None

        device_has_seed = _accepted_capture(state.id, device_id) is not None
        first_seed = state.authority_status == "unseeded"
        missing_device_seed = (
            state.authority_status == "authoritative" and not device_has_seed
        )
        if not (first_seed or missing_device_seed):
            return None

        capture = ub.KoboAnnotationSeedCapture(
            book_state_id=state.id,
            device_id=device_id,
            started_at=_now(),
            device_etag=device_etag,
            result="pending",
            seed_kind="upstream_capture",
        )
        ub.session.add(capture)
        if first_seed:
            state.authority_status = "seeding"
            state.quarantine_reason = None
        ub.session.flush()
        capture_id = capture.id
        if ub.session_commit() is False:
            return None
        kobo_annotation_stage0.record_event(
            "seed_capture", "started", user_id=user_id, book_id=book_id,
        )
        return capture_id
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo annotation seed capture could not start user_id=%s book_id=%s",
            user_id, book_id,
        )
        return None


def _parse_page(raw_body):
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("annotations"), list):
        return None
    next_offset = payload.get("nextPageOffsetToken")
    if next_offset is not None and (
        not isinstance(next_offset, str) or not next_offset
    ):
        return None
    return payload["annotations"], next_offset


def _load_captured_pages(capture_id):
    pages = (
        ub.session.query(ub.KoboAnnotationSeedCapturePage)
        .filter(ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture_id)
        .order_by(ub.KoboAnnotationSeedCapturePage.page_number.asc())
        .all()
    )
    annotations = []
    raw_pages = []
    for expected_number, page in enumerate(pages):
        if page.page_number != expected_number:
            raise ValueError("non-contiguous capture pages")
        raw_body = gzip.decompress(bytes(page.response_body_gzip))
        if hashlib.sha256(raw_body).hexdigest() != page.response_sha256:
            raise ValueError("capture page digest mismatch")
        parsed = _parse_page(raw_body)
        if parsed is None:
            raise ValueError("invalid captured page")
        page_annotations, _next_offset = parsed
        annotations.extend(page_annotations)
        raw_pages.append(raw_body)
    return pages, annotations, raw_pages


def _visible_count(user_id, book_id):
    return (
        ub.session.query(ub.Annotation.id)
        .filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
            (
                ub.Annotation.hidden.is_(None)
                | (ub.Annotation.hidden == False)  # noqa: E712
            ),
        )
        .count()
    )


def _set_failure(capture_id, reason, *, quarantine, log):
    reason = reason if reason in _SAFE_FAILURE_REASONS else "seed_response_invalid"
    capture = ub.session.get(ub.KoboAnnotationSeedCapture, capture_id)
    if capture is None or capture.result != "pending":
        return False
    state = capture.book_state
    capture.completed_at = _now()
    capture.page_count = len(capture.pages)
    capture.result = "rejected" if quarantine else "failed"
    capture.failure_reason = reason
    if quarantine:
        state.authority_status = "quarantined"
        state.quarantine_reason = reason
    elif state.authority_status == "seeding" and not state.ever_authoritative:
        state.authority_status = "unseeded"
    committed = ub.session_commit()
    kobo_annotation_stage0.record_event(
        "seed_capture", "quarantined" if quarantine else "failed",
        user_id=state.user_id, book_id=state.book_id,
    )
    if committed is False:
        log.error(
            "Kobo annotation seed failure state did not commit capture_id=%s",
            capture_id,
        )
    return committed


def seed_coverage(*, user_id, book_state_id):
    """Return active-Kobo capture coverage for mixed-authority diagnostics."""
    active_ids = {
        row[0] for row in (
            ub.session.query(ub.Device.id)
            .filter(
                ub.Device.user_id == user_id,
                ub.Device.kind == "kobo",
                ub.Device.active == True,  # noqa: E712
            )
            .all()
        )
    }
    accepted_ids = {
        row[0] for row in (
            ub.session.query(ub.KoboAnnotationSeedCapture.device_id)
            .filter(
                ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
                ub.KoboAnnotationSeedCapture.result == "accepted",
                ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
                ub.KoboAnnotationSeedCapture.device_id.in_(active_ids),
            )
            .distinct()
            .all()
        )
    } if active_ids else set()
    missing = active_ids - accepted_ids
    return {
        "active_device_count": len(active_ids),
        "accepted_device_count": len(accepted_ids),
        "missing_device_count": len(missing),
        "consistently_local": not missing,
        "books_partially_seeded": int(bool(accepted_ids and missing)),
    }


def _reconcile_and_promote(capture_id, *, book, user, device_id, log):
    capture = ub.session.get(ub.KoboAnnotationSeedCapture, capture_id)
    if capture is None or capture.result != "pending":
        return False
    state = capture.book_state
    try:
        pages, annotations, raw_pages = _load_captured_pages(capture_id)
    except Exception:
        ub.session.rollback()
        return _set_failure(
            capture_id, "seed_response_invalid", quarantine=False, log=log,
        )

    annotation_ids = [
        row.get("id") if isinstance(row, dict) else None for row in annotations
    ]
    if (
        any(not isinstance(value, str) or not value for value in annotation_ids)
        or len(set(annotation_ids)) != len(annotation_ids)
    ):
        return _set_failure(
            capture_id, "seed_duplicate_annotation_id", quarantine=True, log=log,
        )

    try:
        from cps.services import annotation_sync
        from cps.services.kobo_annotation_capture import (
            extract_annotation_materializations,
        )

        raw_by_id = {}
        for raw_page in raw_pages:
            try:
                records = extract_annotation_materializations(
                    raw_page, member_name="annotations",
                )
            except Exception:
                records = []
            raw_by_id.update({record.annotation_id: record for record in records})

        for payload in annotations:
            annotation = annotation_sync._upsert_annotation(
                ub.session,
                payload,
                book,
                user,
                origin_device_id=device_id,
                mark_last_editor=False,
            )
            if annotation is None:
                annotation = (
                    ub.session.query(ub.Annotation)
                    .filter(
                        ub.Annotation.user_id == user.id,
                        ub.Annotation.book_id == book.id,
                        ub.Annotation.annotation_id == payload["id"],
                    )
                    .first()
                )
                if (
                    annotation is not None
                    and annotation.origin_device_id is None
                ):
                    annotation.origin_device_id = device_id
            if annotation is None:
                raise ValueError("captured annotation was not reconciled")
            raw_record = raw_by_id.get(payload["id"])
            if raw_record is not None:
                annotation_sync._store_raw_materialization(
                    ub.session,
                    annotation,
                    raw_record,
                    provenance="kobo_cloud_seed",
                    serveable=True,
                    match_content_revision=True,
                )
        ub.session.flush()
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo annotation seed reconciliation failed capture_id=%s",
            capture_id,
        )
        return _set_failure(
            capture_id, "seed_response_invalid", quarantine=False, log=log,
        )

    visible_count = _visible_count(user.id, book.id)
    captured_count = len(annotations)
    refusal_reason = None
    if len(pages) > 1:
        refusal_reason = "seed_capture_requires_pagination"
    elif visible_count < captured_count:
        refusal_reason = "seed_local_count_below_capture"
    elif visible_count > LOCAL_PAGE_CAPACITY:
        refusal_reason = "seed_local_set_requires_pagination"
    if refusal_reason is not None:
        capture.annotation_count = captured_count
        capture.page_count = len(pages)
        ub.session.flush()
        return _set_failure(capture_id, refusal_reason, quarantine=True, log=log)

    normalized_content_id = _normalized_book_uuid(book)
    conflict = None
    if normalized_content_id is not None:
        conflict = (
            ub.session.query(ub.KoboAnnotationBookState.id)
            .filter(
                ub.KoboAnnotationBookState.user_id == user.id,
                ub.KoboAnnotationBookState.content_id == normalized_content_id,
                ub.KoboAnnotationBookState.id != state.id,
            )
            .first()
        )
    if normalized_content_id is None or conflict is not None:
        return _set_failure(
            capture_id, "seed_content_id_conflict", quarantine=True, log=log,
        )

    now = _now()
    state.content_id = normalized_content_id
    if state.generation_id is None:
        state.generation_id = str(uuid.uuid4())
    state.authority_status = "authoritative"
    state.authority_revision = (state.authority_revision or 0) + 1
    state.ever_authoritative = True
    state.seeded_at = now
    state.quarantine_reason = None
    state.upstream_seed_etag = capture.upstream_etag
    capture.annotation_count = captured_count
    capture.page_count = len(pages)
    capture.completed_at = now
    capture.result = "accepted"
    capture.failure_reason = None
    capture.seed_kind = "upstream_capture"
    if ub.session_commit() is False:
        return False

    coverage = seed_coverage(user_id=user.id, book_state_id=state.id)
    log.info(
        "Kobo annotation seed accepted user_id=%s book_id=%s "
        "annotation_count=%s page_count=%s books_partially_seeded=%s "
        "active_device_count=%s accepted_device_count=%s missing_device_count=%s",
        user.id,
        book.id,
        captured_count,
        len(pages),
        coverage["books_partially_seeded"],
        coverage["active_device_count"],
        coverage["accepted_device_count"],
        coverage["missing_device_count"],
    )
    kobo_annotation_stage0.record_event(
        "seed_capture", "accepted", user_id=user.id, book_id=book.id,
        annotation_count=captured_count,
    )
    return True


def record_proxy_response(
    capture_id, *, response, book, user, device_id, request_offset_token, log,
):
    """Persist one upstream page and finalize a complete capture best-effort."""
    if capture_id is None:
        return False
    try:
        capture = ub.session.get(ub.KoboAnnotationSeedCapture, capture_id)
        if capture is None or capture.result != "pending":
            return False
        if response.status_code < 200 or response.status_code >= 300:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )
        raw_body = response.get_data()
        parsed = _parse_page(raw_body)
        if parsed is None:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )
        _annotations, next_offset = parsed
        if next_offset is not None and next_offset == request_offset_token:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )

        existing = (
            ub.session.query(ub.KoboAnnotationSeedCapturePage)
            .filter(
                ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture_id,
                ub.KoboAnnotationSeedCapturePage.request_offset_token
                == request_offset_token,
            )
            .first()
        )
        digest = hashlib.sha256(raw_body).hexdigest()
        if existing is None:
            page_number = (
                ub.session.query(ub.KoboAnnotationSeedCapturePage.id)
                .filter(
                    ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture_id,
                )
                .count()
            )
            page = ub.KoboAnnotationSeedCapturePage(
                seed_capture_id=capture_id,
                page_number=page_number,
                request_offset_token=request_offset_token,
                response_body_gzip=gzip.compress(raw_body, mtime=0),
                response_sha256=digest,
                response_etag=response.headers.get("ETag"),
                next_offset_token=next_offset,
            )
            ub.session.add(page)
        elif existing.response_sha256 != digest:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )

        capture.upstream_etag = response.headers.get("ETag") or capture.upstream_etag
        capture.response_sha256 = digest
        ub.session.flush()
        if ub.session_commit() is False:
            return False
        if next_offset is not None:
            return True
        return _reconcile_and_promote(
            capture_id, book=book, user=user, device_id=device_id, log=log,
        )
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo annotation seed page persistence failed capture_id=%s",
            capture_id,
        )
        return _set_failure(
            capture_id, "seed_response_invalid", quarantine=False, log=log,
        )
