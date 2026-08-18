# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Behavioural SIGTERM coverage for the cwa-ingest s6 longrun.

The service installs a TERM trap because it owns several background helpers.
Bash defers a trapped signal while a foreground command is running, however,
so the watcher itself must run asynchronously and be waited for explicitly.
These tests execute the real run script with a blocking watcher stub, rather
than pinning a particular shell spelling, because the regression was precisely
that the trap looked correct while never being dispatched.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = (
    REPO_ROOT
    / "root"
    / "etc"
    / "s6-overlay"
    / "s6-rc.d"
    / "cwa-ingest-service"
    / "run"
)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize(
    ("network_share_mode", "expected_watcher"),
    [("true", "watch_fallback.py"), ("false", "inotifywait")],
    ids=["polling-fallback", "inotify"],
)
def test_sigterm_stops_ingest_service_and_its_watcher_tree(
    tmp_path: Path,
    network_share_mode: str,
    expected_watcher: str,
):
    """TERM must stop both watcher paths without waiting for s6's SIGKILL.

    ``cwa-as-abc`` is the only external boundary stubbed here. It records the
    command selected by the real service, then creates a child and blocks just
    like the production Python/inotify watchers. The service must exit within
    two seconds and leave neither level behind.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    watcher_ready = tmp_path / "watcher.ready"
    watcher_pids = tmp_path / "watcher.pids"
    watcher_invocations = tmp_path / "watcher.invocations"
    cwa_as_abc = bin_dir / "cwa-as-abc"
    cwa_as_abc.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$WATCHER_READY_FILE\"\n"
        "printf '%s\\n' \"$*\" >> \"$WATCHER_INVOCATIONS_FILE\"\n"
        "printf '%s\\n' \"$BASHPID\" >> \"$WATCHER_PID_FILE\"\n"
        "sleep 30 &\n"
        "printf '%s\\n' \"$!\" >> \"$WATCHER_PID_FILE\"\n"
        "wait \"$!\"\n"
    )
    cwa_as_abc.chmod(0o755)

    watch_folder = tmp_path / "ingest"
    watch_folder.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "WATCH_FOLDER": str(watch_folder),
            "NETWORK_SHARE_MODE": network_share_mode,
            "CWA_WATCH_MODE": "inotify",
            "CWA_INGEST_RETRY_QUEUE": str(tmp_path / "retry.queue"),
            "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
            "CWA_INGEST_PROCESSING_DIR": str(tmp_path / "processing"),
            "CWA_INGEST_RECENT_DIR": str(tmp_path / "recent"),
            "CWA_INGEST_BATCH_DIRTY_FILE": str(tmp_path / "batch.dirty"),
            "CWA_INGEST_BATCH_LAST_SUCCESS_FILE": str(tmp_path / "batch.success"),
            "CWA_INGEST_BATCH_QUIET_SECONDS": "60",
            "WATCHER_READY_FILE": str(watcher_ready),
            "WATCHER_PID_FILE": str(watcher_pids),
            "WATCHER_INVOCATIONS_FILE": str(watcher_invocations),
        }
    )

    process = subprocess.Popen(
        ["bash", str(RUN_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    timed_out = False
    try:
        deadline = time.monotonic() + 3
        while not watcher_ready.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("ingest watcher did not start within three seconds")
            time.sleep(0.02)

        assert process.poll() is None, "ingest service exited before SIGTERM"
        assert expected_watcher in watcher_ready.read_text()

        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        output = process.communicate(timeout=2)[0]

    assert not timed_out, (
        "cwa-ingest-service was still alive two seconds after SIGTERM; "
        "s6 would eventually SIGKILL the container. Output:\n" + output
    )
    assert process.returncode == 0, output

    recorded_pids = [int(line) for line in watcher_pids.read_text().splitlines()]
    deadline = time.monotonic() + 1
    while any(_pid_exists(pid) for pid in recorded_pids) and time.monotonic() < deadline:
        time.sleep(0.02)
    survivors = [pid for pid in recorded_pids if _pid_exists(pid)]
    assert not survivors, f"watcher descendants survived service shutdown: {survivors}"


def test_inotify_failure_still_falls_back_to_polling(tmp_path: Path):
    """Backgrounding the terminal watcher must not change ``||`` failover."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invocations = tmp_path / "watcher.invocations"
    watcher_pids = tmp_path / "watcher.pids"
    cwa_as_abc = bin_dir / "cwa-as-abc"
    cwa_as_abc.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$WATCHER_INVOCATIONS_FILE\"\n"
        "if [[ \" $* \" == *' inotifywait '* ]]; then exit 23; fi\n"
        "printf '%s\\n' \"$BASHPID\" >> \"$WATCHER_PID_FILE\"\n"
        "sleep 30 &\n"
        "printf '%s\\n' \"$!\" >> \"$WATCHER_PID_FILE\"\n"
        "wait \"$!\"\n"
    )
    cwa_as_abc.chmod(0o755)

    watch_folder = tmp_path / "ingest"
    watch_folder.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "WATCH_FOLDER": str(watch_folder),
            "NETWORK_SHARE_MODE": "false",
            "CWA_WATCH_MODE": "inotify",
            "CWA_INGEST_RETRY_QUEUE": str(tmp_path / "retry.queue"),
            "CWA_INGEST_STATUS_FILE": str(tmp_path / "status"),
            "CWA_INGEST_PROCESSING_DIR": str(tmp_path / "processing"),
            "CWA_INGEST_RECENT_DIR": str(tmp_path / "recent"),
            "CWA_INGEST_BATCH_DIRTY_FILE": str(tmp_path / "batch.dirty"),
            "CWA_INGEST_BATCH_LAST_SUCCESS_FILE": str(tmp_path / "batch.success"),
            "CWA_INGEST_BATCH_QUIET_SECONDS": "60",
            "WATCHER_INVOCATIONS_FILE": str(invocations),
            "WATCHER_PID_FILE": str(watcher_pids),
        }
    )
    process = subprocess.Popen(
        ["bash", str(RUN_SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    try:
        deadline = time.monotonic() + 3
        selected = []
        while time.monotonic() < deadline and process.poll() is None:
            if invocations.exists():
                selected = invocations.read_text().splitlines()
                if len(selected) >= 2:
                    break
            time.sleep(0.02)

        assert process.poll() is None, "service exited instead of failing over"
        assert len(selected) >= 2, f"polling fallback never started: {selected}"
        assert "inotifywait" in selected[0]
        assert "watch_fallback.py" in selected[1]
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        output = process.communicate(timeout=2)[0]

    assert process.returncode == 0, output
    recorded_pids = [int(line) for line in watcher_pids.read_text().splitlines()]
    deadline = time.monotonic() + 1
    while any(_pid_exists(pid) for pid in recorded_pids) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not [pid for pid in recorded_pids if _pid_exists(pid)]
