# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The scratch hardware runbook keeps its load-bearing safety steps explicit."""

from pathlib import Path


RUNBOOK = Path(__file__).parents[2] / "notes" / "ZZWB-ETAG-HARDWARE-EXPERIMENT-RUNBOOK.md"
PROBE = "053742ff-9094-43b2-8511-c0763c90ffab"
PROBE_TITLE = "The Heat Will Kill You First"


def test_runbook_contains_exact_arm_observe_disarm_and_hard_reset_commands():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert PROBE in text
    assert "scripts/zzwb_build_annotation_envelope.py" in text
    assert "W/\"CWNG:<generation-id>:<authority-revision>:<digest-prefix>\"" in text
    assert "docker cp cps/readingservices.py" in text
    assert "/app/calibre-web-automated/cps/readingservices.py" in text
    assert "/config/.cwng-private-observability/kobo-reading-services/" in text
    assert "docs/kobo-reading-services-capture.md" in text
    assert "rm -f /config/zzwb/ARMED" in text
    assert 'docker compose up -d --force-recreate --no-deps "$ZZWB_SERVICE"' in text
    assert "image-readingservices.sha256" in text
    assert "restored-readingservices.sha256" in text
    assert "cmp \"$ZZWB_RUN_DIR/image-readingservices.sha256\"" in text

    disarm = text.index("rm -f /config/zzwb/ARMED")
    stage_payload = text.index("/config/zzwb/payload.json.next", disarm)
    stage_etag = text.index("/config/zzwb/etag.txt.next", stage_payload)
    arm = text.index("touch /config/zzwb/ARMED", stage_etag)
    assert disarm < stage_payload < stage_etag < arm


def test_runbook_pins_only_the_live_probe_and_uses_mac_discovery_commands():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert f"PROBE={PROBE}" in text
    assert "PROBE_BOOK_ID=540" in text
    assert f"PROBE_TITLE='{PROBE_TITLE}'" in text
    assert "KOBO_PILOT=./kobo-pilot" in text
    assert '"$KOBO_PILOT" pull-db --output' in text
    assert '"$KOBO_PILOT" open-book "$PROBE_TITLE"' in text
    assert "discovers the device by its MAC address" in text
    assert "10.0.20.250" not in text
    assert "pull-db --host" not in text
    assert "d83c9bfd" not in text
    assert "91fc0f2b" not in text
    assert "ZZWB Writeback Probe" not in text


def test_runbook_states_the_rigs_remaining_111_and_113_unknowns():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "does not force Nickel to emit a multi-book batch" in text
    assert "mixed authoritative/unseeded/non-owned production algorithm remains untested" in text
    assert "does not derive Kobo's deletion-manifest representation" in text
    assert "W/\"0\"" in text


def test_runbook_pins_mutable_image_by_shipped_hash_and_both_cycle_r_legs():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "ZZWB_CONTAINER=calibre-web" in text
    assert "com.docker.compose.service" in text
    assert 'test -n "$ZZWB_SERVICE"' in text
    assert "ghcr.io/new-usemame/calibre-web-nextgen:dev" in text
    assert "The tag is not a baseline identifier" in text
    assert "be645af98cfdb180" in text
    assert "container-image-id.txt" in text
    assert "dev-1176" not in text
    assert "fourteen-entry" not in text
    assert "changes after every Kobo annotations GET" in text
    assert 'PRE_ETAG="$ZZWB_RUN_DIR/$CYCLE_LABEL-pre-etag.txt"' in text
    assert text.count("snapshot_probe") >= 5
    assert "cycle-r-cwng-etag.txt" in text
    assert "night-r-20260828:91" in text
    assert "printf 'W/\"0\"\\n'" in text
    assert "Nickel rejects `W/\"0\"` specifically" in text
    assert "Nickel did not overwrite the stored token in either run" in text


def test_runbook_builds_one_server_highlight_and_restores_empty_baseline():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "--server-highlight" in text
    assert "exactly one annotation" in text
    assert "visually observe whether Nickel draws" in text
    assert "this probe's own captured create PATCH" in text
    for position_field in (
        "chapterFilename", "startPath", "endPath", "startChar", "endChar",
    ):
        assert position_field in text
    assert "Cycle B cleanup" in text
    assert 'cp "$ZZWB_RUN_DIR/empty-payload.json" "$ZZWB_RUN_DIR/payload.json"' in text
    assert 'cp "$ZZWB_RUN_DIR/cycle-b-pre-etag.txt" "$ZZWB_RUN_DIR/etag.txt"' in text
    assert "cycle-b-pre-bookmark-count.txt" in text
    assert "do not substitute `W/\"0\"`" in text
