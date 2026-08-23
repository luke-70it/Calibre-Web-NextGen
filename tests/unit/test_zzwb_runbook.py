# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The scratch hardware runbook keeps its load-bearing safety steps explicit."""

from pathlib import Path


RUNBOOK = Path(__file__).parents[2] / "notes" / "ZZWB-ETAG-HARDWARE-EXPERIMENT-RUNBOOK.md"
PROBE = "d83c9bfd-91e1-4bed-a1a6-9c50d15ae46c"


def test_runbook_contains_exact_arm_observe_disarm_and_hard_reset_commands():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert PROBE in text
    assert "scripts/zzwb_build_annotation_envelope.py" in text
    assert "W/\"CWNG:<generation-id>:<authority-revision>:<digest-prefix>\"" in text
    assert "docker cp cps/readingservices.py" in text
    assert "/app/calibre-web-automated/cps/readingservices.py" in text
    assert "ZZWB checkforchanges" in text
    assert "rm -f /config/zzwb/ARMED" in text
    assert "docker compose up -d --force-recreate --no-deps calibre-web-automated" in text

    disarm = text.index("rm -f /config/zzwb/ARMED")
    stage_payload = text.index("/config/zzwb/payload.json.next", disarm)
    stage_etag = text.index("/config/zzwb/etag.txt.next", stage_payload)
    arm = text.index("touch /config/zzwb/ARMED", stage_etag)
    assert disarm < stage_payload < stage_etag < arm


def test_runbook_states_the_rigs_remaining_111_and_113_unknowns():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "does not force Nickel to emit a multi-book batch" in text
    assert "mixed authoritative/unseeded/non-owned production algorithm remains untested" in text
    assert "does not derive Kobo's deletion-manifest representation" in text
    assert "W/\"0\"" in text
