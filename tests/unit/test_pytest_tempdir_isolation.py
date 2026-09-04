# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every pytest process gets its own tempdir, so the machine-global singletons
in scripts/ cannot be contended by anything outside this process."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tests import conftest as pytest_conftest

_isolate_pytest_tempdir = pytest_conftest._isolate_pytest_tempdir

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_real_external_volume(monkeypatch):
    """Keep these unit tests independent of the host's actual mounted volumes."""
    monkeypatch.delenv("CWNG_PYTEST_TMP_BASE", raising=False)
    monkeypatch.setattr(
        pytest_conftest,
        "_pytest_external_volume_is_mounted",
        lambda _volume_root: False,
    )


@pytest.mark.unit
def test_tempdir_is_private_and_both_channels_agree(monkeypatch, tmp_path):
    """Half-application is the failure mode this pins.

    ``gettempdir()`` caches into ``tempfile.tempdir``, so setting only the
    environment variable leaves this process on the shared path and setting only
    the module global leaves subprocesses on it.  Either alone fixes half the
    contention and reads as "the fixture does not quite work".
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    root = _isolate_pytest_tempdir()

    assert str(os.getpid()) in root, root
    assert tempfile.gettempdir() == root, "this process still resolves the shared tempdir"
    assert os.environ["TMPDIR"] == root, "subprocesses still inherit the shared tempdir"
    assert Path(root).is_dir(), "the private tempdir was never created"


@pytest.mark.unit
def test_it_applies_without_xdist(monkeypatch, tmp_path):
    """The measured failure was a SERIAL run losing to an outside lock holder.

    An earlier version returned early when PYTEST_XDIST_WORKER was absent, which
    skipped exactly the case that was reproduced: 12 failed, 57 passed serially
    with the lock held by an unrelated process.
    """
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    root = _isolate_pytest_tempdir()

    assert root is not None, "a serial run is not exempt: the lock is machine-global"
    assert tempfile.gettempdir() == root


@pytest.mark.unit
def test_the_key_is_the_process_not_the_worker_id(monkeypatch, tmp_path):
    """Two concurrent sessions both have a ``gw0``; they do not share a PID."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")

    root = _isolate_pytest_tempdir()

    assert "gw0" not in Path(root).name, (
        "keying on the worker id makes two concurrent sessions collide on gw0"
    )
    assert Path(root).name == str(os.getpid())


@pytest.mark.unit
def test_explicit_base_override_wins(monkeypatch, tmp_path, capsys):
    override_base = tmp_path / "override"
    monkeypatch.setenv("CWNG_PYTEST_TMP_BASE", str(override_base))
    monkeypatch.setattr(
        pytest_conftest,
        "_pytest_external_volume_is_mounted",
        lambda _volume_root: pytest.fail(
            "the volume must not be probed with an override"
        ),
    )

    root = Path(_isolate_pytest_tempdir())

    assert root.parent == override_base
    assert capsys.readouterr().err == (
        f"pytest temp base: {override_base} (CWNG_PYTEST_TMP_BASE is set)\n"
    )


@pytest.mark.unit
def test_mounted_writable_volume_uses_external_base(monkeypatch, tmp_path, capsys):
    system_tmp = tmp_path / "system"
    volume_root = tmp_path / "mounted-volume"
    monkeypatch.setattr(tempfile, "tempdir", str(system_tmp))
    monkeypatch.setattr(
        pytest_conftest, "_PYTEST_EXTERNAL_VOLUME_ROOT", str(volume_root)
    )
    monkeypatch.setattr(
        pytest_conftest,
        "_pytest_external_volume_is_mounted",
        lambda candidate: candidate == str(volume_root),
    )

    root = Path(_isolate_pytest_tempdir())
    expected_base = volume_root / "agent-scratch" / "cwng-pytest"

    assert root.parent == expected_base
    assert expected_base.is_dir()
    assert capsys.readouterr().err == (
        f"pytest temp base: {expected_base} "
        f"({volume_root} is mounted and writable)\n"
    )


@pytest.mark.unit
def test_mounted_unwritable_volume_falls_back_and_reports_why(
    monkeypatch, tmp_path, capsys
):
    system_tmp = tmp_path / "system"
    volume_root = tmp_path / "mounted-volume"
    external_base = volume_root / "agent-scratch" / "cwng-pytest"
    monkeypatch.setattr(tempfile, "tempdir", str(system_tmp))
    monkeypatch.setattr(
        pytest_conftest, "_PYTEST_EXTERNAL_VOLUME_ROOT", str(volume_root)
    )
    monkeypatch.setattr(
        pytest_conftest,
        "_pytest_external_volume_is_mounted",
        lambda candidate: candidate == str(volume_root),
    )

    def deny_external_write(candidate, mode):
        assert candidate == str(external_base)
        assert mode == os.W_OK
        return False

    monkeypatch.setattr(os, "access", deny_external_write)

    root = Path(_isolate_pytest_tempdir())
    fallback_base = system_tmp / "cwng-pytest"

    assert root.parent == fallback_base
    assert capsys.readouterr().err == (
        f"pytest temp base: {fallback_base} ({volume_root} is mounted but "
        f"{external_base} is not writable)\n"
    )


@pytest.mark.unit
def test_missing_external_volume_uses_system_tempdir(monkeypatch, tmp_path, capsys):
    system_tmp = tmp_path / "system"
    volume_root = tmp_path / "missing-volume"
    monkeypatch.setattr(tempfile, "tempdir", str(system_tmp))
    monkeypatch.setattr(
        pytest_conftest, "_PYTEST_EXTERNAL_VOLUME_ROOT", str(volume_root)
    )

    root = Path(_isolate_pytest_tempdir())
    fallback_base = system_tmp / "cwng-pytest"

    assert root.parent == fallback_base
    assert capsys.readouterr().err == (
        f"pytest temp base: {fallback_base} ({volume_root} is not mounted)\n"
    )


@pytest.mark.unit
def test_the_singletons_this_protects_still_key_on_gettempdir():
    """If a guard stops deriving its path from gettempdir(), this fixture stops
    covering it -- and the coverage loss would otherwise be silent."""
    guarded = (
        "scripts/ingest_processor.py",
        "scripts/cover_enforcer.py",
        "scripts/convert_library.py",
        "scripts/kindle_epub_fixer.py",
    )
    missing = [
        rel for rel in guarded
        if "tempfile.gettempdir()" not in (REPO / rel).read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"these no longer key a lock on gettempdir(): {missing} -- either they "
        f"moved to a safe path (drop them here) or to another shared one (this "
        f"fixture no longer protects them)"
    )
