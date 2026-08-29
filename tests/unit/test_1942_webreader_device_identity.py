# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""#1942 M1: a browser installation is a private, first-class Device."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import flask
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


INSTALLATION_A = "11111111-1111-4111-8111-111111111111"
INSTALLATION_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def registry(tmp_path):
    from cps import ub

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    original = ub.session
    ub.session = session
    app = flask.Flask(__name__)
    app.secret_key = "issue-1942-test-secret"
    try:
        with app.app_context():
            yield session
    finally:
        session.close()
        ub.session = original
        engine.dispose()


def test_webreader_hmac_is_exact_and_deterministic():
    from cps.services.device_registry import _webreader_fingerprint

    secret = b"deterministic-secret"
    expected = hmac.new(
        secret,
        b"cwng-device:webreader:v1\0" + INSTALLATION_A.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert _webreader_fingerprint(INSTALLATION_A, secret) == expected
    assert _webreader_fingerprint(INSTALLATION_A, secret) == expected
    assert _webreader_fingerprint(INSTALLATION_B, secret) != expected
    assert _webreader_fingerprint(INSTALLATION_A, b"another-secret") != expected


def test_installation_creates_device_identity_without_storing_raw_id(registry):
    from cps import ub
    from cps.services.device_registry import (
        WEBREADER_SCHEME,
        ensure_webreader_device_best_effort,
    )

    device_id = ensure_webreader_device_best_effort(
        user_id=7,
        installation_id=INSTALLATION_A,
    )
    registry.expire_all()
    device = registry.query(ub.Device).filter_by(id=device_id).one()
    identity = registry.query(ub.DeviceIdentity).filter_by(device_id=device_id).one()

    assert device.kind == "webreader"
    assert device.created_by == "auto"
    assert device.display_name == "Web reader"
    assert identity.scheme == WEBREADER_SCHEME
    assert identity.key_version == 1
    assert len(identity.fingerprint) == 64
    stored_values = (
        device.public_id,
        device.display_name,
        device.model,
        device.platform,
        identity.scheme,
        identity.fingerprint,
    )
    assert all(INSTALLATION_A not in (value or "") for value in stored_values)


def test_same_installation_is_stable_and_two_browsers_are_separate(registry):
    from cps import ub
    from cps.services.device_registry import ensure_webreader_device_best_effort

    first = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_A)
    first_again = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_A)
    second = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_B)

    assert first_again == first
    assert second != first
    devices = registry.query(ub.Device).filter_by(user_id=7, kind="webreader").all()
    assert {device.id for device in devices} == {first, second}
    assert {device.display_name for device in devices} == {"Web reader", "Web reader 2"}
    assert registry.query(ub.DeviceIdentity).count() == 2


def test_missing_installation_id_keeps_a_distinct_legacy_singleton(registry):
    from cps import ub
    from cps.services.device_registry import ensure_webreader_device_best_effort

    browser = ensure_webreader_device_best_effort(user_id=7, installation_id=INSTALLATION_A)
    legacy = ensure_webreader_device_best_effort(user_id=7)
    legacy_again = ensure_webreader_device_best_effort(user_id=7, installation_id=None)

    assert legacy_again == legacy
    assert legacy != browser
    assert registry.query(ub.DeviceIdentity).filter_by(device_id=legacy).count() == 0
    assert registry.query(ub.Device).filter_by(user_id=7, kind="webreader").count() == 2


def test_repeated_position_observations_throttle_last_seen_writes(registry):
    from cps.services.device_registry import upsert_webreader_device

    first_seen = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    device = upsert_webreader_device(
        registry,
        user_id=7,
        installation_id=INSTALLATION_A,
        secret_key="issue-1942-test-secret",
        seen_at=first_seen,
    )
    registry.commit()
    upsert_webreader_device(
        registry,
        user_id=7,
        installation_id=INSTALLATION_A,
        secret_key="issue-1942-test-secret",
        seen_at=first_seen + timedelta(seconds=1),
    )
    assert device.last_seen_at.replace(tzinfo=timezone.utc) == first_seen
    assert not registry.dirty

    upsert_webreader_device(
        registry,
        user_id=7,
        installation_id=INSTALLATION_A,
        secret_key="issue-1942-test-secret",
        seen_at=first_seen + timedelta(minutes=5),
    )
    assert device.last_seen_at.replace(tzinfo=timezone.utc) == first_seen + timedelta(minutes=5)


def test_origin_index_migration_is_additive_and_idempotent():
    from cps import ub

    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE annotation ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, book_id INTEGER, "
            "origin_device_id INTEGER)"
        ))
    ub.migrate_webreader_device_identity_slice(engine, None)
    ub.migrate_webreader_device_identity_slice(engine, None)
    with engine.connect() as conn:
        indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(annotation)"))}
    assert "ix_annotation_user_book_origin" in indexes
