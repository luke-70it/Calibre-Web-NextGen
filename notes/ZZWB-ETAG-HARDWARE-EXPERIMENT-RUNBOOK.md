# ZZWB arbitrary-ETag hardware experiment — run, observe, and revert

> **SCRATCH INSTRUMENT — NEVER MERGE.** This procedure is only for `ZZWB Writeback Probe`,
> ContentId `d83c9bfd-91e1-4bed-a1a6-9c50d15ae46c`. It hot-copies one scratch source file into one
> deployed container. It is not a production writeback design.

## 1. Tonight's baseline and safety boundary

- **[OBSERVED 2026-08-28]** The deployed image is `dev-1176`, which is the current `origin/main`
  baseline used by this rig. `cps/readingservices.py` in this worktree contains that production
  implementation plus the arming-file-gated probe hook; do not copy the August 23 version.
- **[OBSERVED 2026-08-28]** The probe's stored `AnnotationsSyncToken` is currently a fourteen-entry
  Kobo composite manifest, **not** `W/"0"`. Record the complete token from the pre-run device
  snapshot. Never reconstruct it from another book or from memory.
- **[OBSERVED]** A named annotations GET returning 304, 503, or hanging caused Nickel to replace
  that book's local set with empty. The rig names the probe only when `/config/zzwb/ARMED`,
  `payload.json`, and `etag.txt` all exist and validate.
- **[OBSERVED IN TESTS]** A ready probe GET ignores conditional request headers and returns 200 with
  the staged bytes and staged ETag. It does not contact Kobo upstream.
- **[OBSERVED IN TESTS]** Both GET and `checkforchanges` reject every non-probe ContentId before
  inspecting the arming directory. When unarmed, the probe GET returns the production proxy object
  unchanged and `checkforchanges` returns the same status/body/header triplet as `origin/main`.
- **[OBSERVED IN `dev-1176`]** private exchange capture is already enabled. The rig deliberately
  has no second `ZZWB checkforchanges` capture stream. The existing schema-version-2 records under
  `/config/.cwng-private-observability/kobo-reading-services/` capture the device request, filtered
  upstream leg, decisions, and exact final response. The staged GET also attaches this observer.

The authored tag spelling remains:

```text
W/"CWNG:<generation-id>:<authority-revision>:<digest-prefix>"
```

Every cycle follows the same order: reader offline, disarm, atomically replace both stage files,
arm last, then restore connectivity. Never edit a stage while the reader can sync.

## 2. Prepare the worktree and record the device baseline

Run from **this worktree**, not another checkout:

```bash
test "$(git branch --show-current)" = "scratch/zzwb-stage3b-20260828"
rg -q 'ZZWB_EXPERIMENT_UUID' cps/readingservices.py
python -m pytest \
  tests/unit/test_zzwb*.py \
  tests/unit/test_kobo_reading_services*.py -q

install -d -m 700 ./zzwb-run
ZZWB_CONTAINER=calibre-web-nextgen
ZZWB_RUN_DIR=$PWD/zzwb-run
KOBO_PILOT=/path/to/project-root/tools/kobo-pilot/kobo-pilot
PROBE=d83c9bfd-91e1-4bed-a1a6-9c50d15ae46c
```

Pull the baseline while the reader is offline. The wrapper copies the database, WAL, and SHM and
integrity-checks the local copy:

```bash
"$KOBO_PILOT" pull-db --host 10.0.20.250 \
  --output "$ZZWB_RUN_DIR/device-before"
sqlite3 -readonly "$ZZWB_RUN_DIR/device-before/KoboReader.sqlite" \
  "SELECT ContentID,AnnotationsSyncToken,IsDownloaded FROM content WHERE ContentID='$PROBE';" \
  | tee "$ZZWB_RUN_DIR/device-before-content.txt"
sqlite3 -readonly "$ZZWB_RUN_DIR/device-before/KoboReader.sqlite" \
  "SELECT AnnotationsSyncToken FROM content WHERE ContentID='$PROBE';" \
  >"$ZZWB_RUN_DIR/baseline-etag.txt"
sqlite3 -readonly "$ZZWB_RUN_DIR/device-before/KoboReader.sqlite" \
  "SELECT COUNT(*) FROM Bookmark WHERE VolumeID LIKE '$PROBE%';" \
  | tee "$ZZWB_RUN_DIR/device-before-bookmark-count.txt"
python3 -c '
from pathlib import Path
p=Path("zzwb-run/baseline-etag.txt")
lines=p.read_text().splitlines()
assert len(lines)==1 and lines[0].startswith("W/\"") and lines[0] != "W/\"0\"", lines
print({"baseline_etag_bytes":len(lines[0].encode("ascii"))})
'
chmod 600 "$ZZWB_RUN_DIR/baseline-etag.txt"
```

The exact-token assertion is deliberately probe-specific. A fourteen-entry manifest observed on a
different book is not acceptable baseline evidence.

Create the measured empty envelope used by both Cycle R legs and final cleanup:

```bash
python3 -c 'from pathlib import Path; Path("zzwb-run/empty-payload.json").write_bytes(b"{\"annotations\":[],\"nextPageOffsetToken\":null}")'
chmod 600 "$ZZWB_RUN_DIR/empty-payload.json"
shasum -a 256 "$ZZWB_RUN_DIR/empty-payload.json"
```

The builder still supports exact Stage 0 materializations from an offline `app.db` snapshot:

```bash
ZZWB_DB_SNAPSHOT=/path/to/offline/app.db
./scripts/zzwb_build_annotation_envelope.py \
  --database "$ZZWB_DB_SNAPSHOT" \
  --output "$ZZWB_RUN_DIR/captured-payload.json" \
  --force
```

It opens SQLite with `mode=ro` and `PRAGMA query_only=ON`, selects only the fixed probe, verifies
captured-object and raw-location digests, rejects non-captured provenance, and refuses an empty set
unless `--allow-empty` is explicit.

## 3. Prove the hot-copy source and deployed image baseline

The source of the hot-copy is exactly `cps/readingservices.py` in this worktree after the
`origin/main` integration. Record the configured image and image-shipped file hash before
overwriting it:

```bash
docker inspect --format '{{.Config.Image}}' "$ZZWB_CONTAINER" \
  | tee "$ZZWB_RUN_DIR/container-image.txt"
rg -q 'dev-1176' "$ZZWB_RUN_DIR/container-image.txt"
docker exec "$ZZWB_CONTAINER" sha256sum \
  /app/calibre-web-automated/cps/readingservices.py \
  >"$ZZWB_RUN_DIR/image-readingservices.sha256"

docker cp cps/readingservices.py \
  "$ZZWB_CONTAINER:/app/calibre-web-automated/cps/readingservices.py"
docker exec "$ZZWB_CONTAINER" \
  python3 -m py_compile /app/calibre-web-automated/cps/readingservices.py
docker restart "$ZZWB_CONTAINER"
```

Wait for the ordinary health check to report healthy. Exchange capture is already on in
`dev-1176`; do not add or change its environment gate. Then create the private stage directory and
prove it is disarmed:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  install -d -m 700 -o abc -g abc /config/zzwb
  rm -f /config/zzwb/ARMED
  test ! -e /config/zzwb/ARMED
'
docker inspect --format '{{.State.Health.Status}}' "$ZZWB_CONTAINER"
```

## 4. Stage and arm one response

Set `$ZZWB_RUN_DIR/payload.json` and `$ZZWB_RUN_DIR/etag.txt` for the cycle, keep the reader
offline, then run this exact order:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c 'rm -f /config/zzwb/ARMED'
docker cp "$ZZWB_RUN_DIR/payload.json" \
  "$ZZWB_CONTAINER:/config/zzwb/payload.json.next"
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  chown abc:abc /config/zzwb/payload.json.next
  chmod 600 /config/zzwb/payload.json.next
  mv /config/zzwb/payload.json.next /config/zzwb/payload.json
'
docker cp "$ZZWB_RUN_DIR/etag.txt" \
  "$ZZWB_CONTAINER:/config/zzwb/etag.txt.next"
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  chown abc:abc /config/zzwb/etag.txt.next
  chmod 600 /config/zzwb/etag.txt.next
  mv /config/zzwb/etag.txt.next /config/zzwb/etag.txt
  touch /config/zzwb/ARMED
  chown abc:abc /config/zzwb/ARMED
  chmod 600 /config/zzwb/ARMED
'
```

Verify the armed files inside the container without printing annotation text:

```bash
docker exec "$ZZWB_CONTAINER" python3 -c '
import hashlib,json,pathlib
d=pathlib.Path("/config/zzwb")
p=d.joinpath("payload.json").read_bytes()
e=d.joinpath("etag.txt").read_text("ascii").strip()
o=json.loads(p)
assert isinstance(o,dict) and isinstance(o.get("annotations"),list)
assert "nextPageOffsetToken" in o
assert d.joinpath("ARMED").is_file()
print({"bytes":len(p),"sha256":hashlib.sha256(p).hexdigest(),"etag":e,
       "annotations":len(o["annotations"])})
'
```

## 5. Observe a cycle through the existing exchange capture

Set a unique label for the leg (`cycle-r-cwng`, `cycle-r-zero`, `cycle-b`, or
`cycle-b-cleanup`). Immediately before restoring Wi-Fi, record a UTC start time and current capture
filenames:

```bash
test -n "$CYCLE_LABEL"
CYCLE_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker exec "$ZZWB_CONTAINER" sh -c \
  'find /config/.cwng-private-observability/kobo-reading-services -type f -name "exchange-*.json.gz" -print | sort' \
  >"$ZZWB_RUN_DIR/$CYCLE_LABEL-captures-before.txt"
```

For each leg:

1. Restore Wi-Fi and tap **Sync now** once.
2. Open `ZZWB Writeback Probe`, close it fully, and reopen it.
3. Tap **Sync now** again without altering the stage.
4. Close/reopen once more and tap **Sync now** a third time.

The pilot can open the exact title when Nickel is at Home:

```bash
"$KOBO_PILOT" open-book "ZZWB Writeback Probe" \
  --host 10.0.20.250 --expect-book-title "ZZWB Writeback Probe"
```

Collect structural telemetry and preserve private capture artifacts only in `zzwb-run`:

```bash
docker logs --since "$CYCLE_START" "$ZZWB_CONTAINER" 2>&1 \
  | tee "$ZZWB_RUN_DIR/$CYCLE_LABEL.log"
rg 'ZZWB: serving staged annotations|ZZWB: naming' \
  "$ZZWB_RUN_DIR/$CYCLE_LABEL.log"
docker exec "$ZZWB_CONTAINER" sh -c \
  'find /config/.cwng-private-observability/kobo-reading-services -type f -name "exchange-*.json.gz" -print | sort' \
  >"$ZZWB_RUN_DIR/$CYCLE_LABEL-captures-after.txt"
comm -13 "$ZZWB_RUN_DIR/$CYCLE_LABEL-captures-before.txt" \
  "$ZZWB_RUN_DIR/$CYCLE_LABEL-captures-after.txt" \
  >"$ZZWB_RUN_DIR/$CYCLE_LABEL-captures-new.txt"
install -d -m 700 "$ZZWB_RUN_DIR/captures-$CYCLE_LABEL"
while IFS= read -r record; do
  test -n "$record" || continue
  docker cp "$ZZWB_CONTAINER:$record" "$ZZWB_RUN_DIR/captures-$CYCLE_LABEL/"
done <"$ZZWB_RUN_DIR/$CYCLE_LABEL-captures-new.txt"
chmod 600 "$ZZWB_RUN_DIR"/captures-"$CYCLE_LABEL"/exchange-*.json.gz
```

Use `docs/kobo-reading-services-capture.md` as the schema contract. Load the gzip JSON locally and
inspect only records whose device request path contains the exact probe. For `checkforchanges`, the
load-bearing fact is the incoming `(ContentId, etag)` pair for this probe. For annotations GET,
require the exact staged ETag/body digest in `device_response`, with no `upstream_request` leg. The
local DB token is corroboration, not a substitute for the captured incoming echo.

## 6. Cycle R — rollback discriminator, two separate runs

Cycle R uses the same empty body in both legs. Only the ETag changes, isolating token acceptance
from annotation-set replacement.

### R1: a different CWNG revision

Stage an unmistakably new revision, different from every CWNG token adopted in earlier runs:

```bash
cp "$ZZWB_RUN_DIR/empty-payload.json" "$ZZWB_RUN_DIR/payload.json"
DIGEST_PREFIX=$(shasum -a 256 "$ZZWB_RUN_DIR/payload.json" | awk '{print substr($1,1,16)}')
printf 'W/"CWNG:night-r-20260828:91:%s"\n' "$DIGEST_PREFIX" \
  >"$ZZWB_RUN_DIR/etag.txt"
cp "$ZZWB_RUN_DIR/etag.txt" "$ZZWB_RUN_DIR/cycle-r-cwng-etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt" \
  "$ZZWB_RUN_DIR/cycle-r-cwng-etag.txt"
CYCLE_LABEL=cycle-r-cwng
```

Repeat sections 4 and 5. Pull `device-after-r-cwng`, require zero probe Bookmark rows, and require a
later captured `checkforchanges` request to echo `cycle-r-cwng-etag.txt` byte-for-byte. An echoed
tag proves Nickel can overwrite its previous stored token; absence proves nothing unless capture
also shows the staged GET completed.

### R2: the empty-set token `W/"0"`

Keep the identical empty body and change only the tag:

```bash
cp "$ZZWB_RUN_DIR/empty-payload.json" "$ZZWB_RUN_DIR/payload.json"
printf 'W/"0"\n' >"$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
CYCLE_LABEL=cycle-r-zero
```

Repeat sections 4 and 5 as a new capture batch. Pull `device-after-r-zero` and require zero probe
Bookmark rows. Interpret only after proving both staged GETs completed:

| R1 different CWNG revision | R2 `W/"0"` | Discriminator |
|---|---|---|
| echoed/adopted | not echoed; R1 remains | **Nickel rejects `W/"0"` specifically.** |
| not adopted | not adopted | **Nickel did not overwrite the stored token in either run.** |
| adopted | adopted | Nickel overwrites stored tokens and accepts `W/"0"`. |

Any run without a captured staged GET is inconclusive, not evidence for either branch.

## 7. Cycle B — one server-authored highlight under a new revision

Create `server-highlight.json` from position values measured for this exact probe KEPUB. The object
must use the successful Kobo GET highlight shape: exactly `attachments`,
`clientLastModifiedUtc`, top-level `context`, `highlightColor`, `highlightedText`, `id`, `location`,
and `type`; `location` contains exactly one `span` with `chapterFilename`, `chapterProgress`,
`startPath`, `endPath`, `startChar`, and `endChar`. `attachments` is `{}`, `type` is `highlight`,
`id` is a new canonical lower-case UUID, `clientLastModifiedUtc` is UTC RFC 3339 ending in `Z`, and
`highlightColor` is one of the five measured Kobo wire hex values. Do not use placeholder
chapter/path/text values: review the spec against a probe capture before staging it.

The builder validates the exact field set, span shape, range, empty attachments, and measured color
palette, and emits exactly one annotation:

```bash
./scripts/zzwb_build_annotation_envelope.py \
  --server-highlight "$ZZWB_RUN_DIR/server-highlight.json" \
  --output "$ZZWB_RUN_DIR/payload.json" \
  --force
python3 -c '
import json,pathlib
o=json.loads(pathlib.Path("zzwb-run/payload.json").read_bytes())
assert len(o["annotations"]) == 1
print({"annotations":1,"id":o["annotations"][0]["id"],
       "color":o["annotations"][0]["highlightColor"]})
'
DIGEST_PREFIX=$(shasum -a 256 "$ZZWB_RUN_DIR/payload.json" | awk '{print substr($1,1,16)}')
printf 'W/"CWNG:night-b-20260828:101:%s"\n' "$DIGEST_PREFIX" \
  >"$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
CYCLE_LABEL=cycle-b
```

Repeat sections 4 and 5. The first request should declare the token left by Cycle R, the GET must
serve the one-row payload and Cycle B ETag, and later requests should echo Cycle B exactly. Pull a
fresh device database and compare the staged `id`, text-free location fields, and color to the
probe's Bookmark row. Then open the exact chapter/page and **visually observe whether Nickel draws
the highlight**; a database row alone does not establish rendering.

This is **[ASSUMED SAFE ONLY FOR THIS DISPOSABLE PROBE]** and is not authority evidence for any
other book, firmware, or device.

## 8. Cycle B cleanup — return the probe to its empty baseline set

Put the reader offline. Stage the empty body and the exact pre-run composite token recorded in
`baseline-etag.txt`; do not substitute `W/"0"` regardless of Cycle R's outcome:

```bash
cp "$ZZWB_RUN_DIR/empty-payload.json" "$ZZWB_RUN_DIR/payload.json"
cp "$ZZWB_RUN_DIR/baseline-etag.txt" "$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
CYCLE_LABEL=cycle-b-cleanup
```

Repeat sections 4 and 5, then sync/close/open twice. Do not remove the rig until a fresh read-only
snapshot proves:

```text
Bookmark count for the probe = the pre-run count (expected zero; use the recorded artifact)
AnnotationsSyncToken for the exact probe = the exact baseline-etag.txt bytes
later checkforchanges request for the exact probe = the exact baseline-etag.txt bytes
```

The changed-set cleanup requirement is Bookmark-set equality. Exact token restoration is an
additional byte-for-byte baseline check and must use this book's own saved token.

## 9. Disarm, preserve evidence, and restore image bytes

Keep the reader offline until container recreation and byte verification complete:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c 'rm -f /config/zzwb/ARMED'
docker cp "$ZZWB_CONTAINER:/config/zzwb" "$ZZWB_RUN_DIR/stage-final"
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  rm -f /config/zzwb/payload.json /config/zzwb/etag.txt
  rm -f /config/zzwb/payload.json.next /config/zzwb/etag.txt.next
  rmdir /config/zzwb 2>/dev/null || true
'
docker compose up -d --force-recreate --no-deps calibre-web-automated
```

Verify the replacement has no scratch marker or arm, is healthy, uses `dev-1176`, and contains the
exact bytes recorded before the hot-copy:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  ! grep -q ZZWB_EXPERIMENT_UUID /app/calibre-web-automated/cps/readingservices.py
  test ! -e /config/zzwb/ARMED
'
docker inspect --format '{{.Config.Image}} {{.State.Health.Status}}' "$ZZWB_CONTAINER" \
  | tee "$ZZWB_RUN_DIR/restored-container.txt"
rg -q 'dev-1176 .*healthy' "$ZZWB_RUN_DIR/restored-container.txt"
docker exec "$ZZWB_CONTAINER" sha256sum \
  /app/calibre-web-automated/cps/readingservices.py \
  >"$ZZWB_RUN_DIR/restored-readingservices.sha256"
cmp "$ZZWB_RUN_DIR/image-readingservices.sha256" \
  "$ZZWB_RUN_DIR/restored-readingservices.sha256"
```

If `/app/calibre-web-automated` is bind-mounted, recreation cannot discard the hot-copy. Restore the
bind from image `dev-1176`, restart, and require the same `cmp` before putting the reader online.

## 10. What the rig does and does not establish

- **Can answer with Cycle R:** whether Nickel overwrites a stored annotation token with a different
  CWNG revision, and—under the same empty payload—whether it rejects `W/"0"` specifically.
- **Can answer with Cycle B:** whether Nickel adopts and visibly renders one exact-Kobo-shape,
  server-authored highlight under a new CWNG revision, and whether cleanup restores the empty set.
- **Can observe:** natural `checkforchanges` arrays through production exchange capture.
- It does not force Nickel to emit a multi-book batch.
- The mixed authoritative/unseeded/non-owned production algorithm remains untested.
- It does not derive Kobo's deletion-manifest representation; arbitrary CWNG revisions make that
  derivation unnecessary for this experiment.
- It does not establish other firmware/models, multi-device convergence, pagination, offline edits,
  or permission to ship writeback.
