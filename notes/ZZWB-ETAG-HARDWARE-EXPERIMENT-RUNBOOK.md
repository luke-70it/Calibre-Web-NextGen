# ZZWB arbitrary-ETag hardware experiment — run, observe, and revert

> **SCRATCH INSTRUMENT — NEVER MERGE.** This procedure is only for `The Heat Will Kill You First`,
> Calibre book 540, ContentId `053742ff-9094-43b2-8511-c0763c90ffab`. It hot-copies one scratch
> source file into one deployed container. It is not a production writeback design.

## 1. Tonight's baseline and safety boundary

- **[OBSERVED 2026-08-28]** The deployed container is `calibre-web`, configured from the mutable
  tag `ghcr.io/new-usemame/calibre-web-nextgen:dev`. The tag is not a baseline identifier. The
  exact SHA-256 of its image-shipped `readingservices.py` is recorded before the hot-copy; the
  currently deployed hash begins `be645af98cfdb180`.
- **[OBSERVED 2026-08-28]** `cps/readingservices.py` in this scratch worktree contains the image's
  production path plus the arming-file-gated hook. The unarmed identity tests must pass before it
  is copied; do not use a source file from another checkout.
- **[OBSERVED 2026-08-28]** The live probe's stored `AnnotationsSyncToken` is a Kobo composite
  manifest that changes after every Kobo annotations GET. Pull the device database while offline
  immediately before **each** Cycle R leg and Cycle B, and save that cycle's complete token. Never
  reuse a previous cycle's token or reconstruct one from another book or from memory.
- **[OBSERVED]** A named annotations GET returning 304, 503, or hanging caused Nickel to replace
  that book's local set with empty. The rig names the probe only when `/config/zzwb/ARMED`,
  `payload.json`, and `etag.txt` all exist and validate.
- **[OBSERVED IN TESTS]** A ready probe GET ignores conditional request headers and returns 200 with
  the staged bytes and staged ETag. It does not contact Kobo upstream.
- **[OBSERVED IN TESTS]** Both GET and `checkforchanges` reject every non-probe ContentId before
  inspecting the arming directory. When unarmed, the probe GET returns the production proxy object
  unchanged and `checkforchanges` returns the same status/body/header triplet as `origin/main`.
- **[OBSERVED IN THE DEPLOYED CONTAINER]** private exchange capture is already enabled. The rig deliberately
  has no second `ZZWB checkforchanges` capture stream. The existing schema-version-2 records under
  `/config/.cwng-private-observability/kobo-reading-services/` capture the device request, filtered
  upstream leg, decisions, and exact final response. The staged GET also attaches this observer.

The authored tag spelling remains:

```text
W/"CWNG:<generation-id>:<authority-revision>:<digest-prefix>"
```

Every cycle follows the same order: reader offline, disarm, atomically replace both stage files,
arm last, then restore connectivity. Never edit a stage while the reader can sync.

## 2. Prepare the worktree and define the per-cycle device snapshot

Run from **this worktree**, not another checkout:

```bash
test "$(git branch --show-current)" = "scratch/zzwb-stage3b-20260828"
rg -q 'ZZWB_EXPERIMENT_UUID' cps/readingservices.py
python -m pytest \
  tests/unit/test_zzwb*.py \
  tests/unit/test_kobo_reading_services*.py -q

install -d -m 700 ./zzwb-run
ZZWB_CONTAINER=calibre-web
ZZWB_SERVICE=$(docker inspect --format \
  '{{ index .Config.Labels "com.docker.compose.service" }}' "$ZZWB_CONTAINER")
test -n "$ZZWB_SERVICE"
ZZWB_RUN_DIR=$PWD/zzwb-run
KOBO_PILOT=./kobo-pilot
PROBE=053742ff-9094-43b2-8511-c0763c90ffab
PROBE_BOOK_ID=540
PROBE_TITLE='The Heat Will Kill You First'
test -x "$KOBO_PILOT"
```

`./kobo-pilot` discovers the device by its MAC address. Do not pass a remembered IP address or a
`--host` option.

Define this snapshot helper once in the same shell. The wrapper copies the database, WAL, and SHM
and integrity-checks the local copy. The cycle label makes the rollback token and Bookmark count
unambiguous:

```bash
snapshot_probe() {
  test -n "$CYCLE_LABEL"
  DEVICE_BEFORE_DIR="$ZZWB_RUN_DIR/device-before-$CYCLE_LABEL"
  PRE_ETAG="$ZZWB_RUN_DIR/$CYCLE_LABEL-pre-etag.txt"
  "$KOBO_PILOT" pull-db --output "$DEVICE_BEFORE_DIR"
  sqlite3 -readonly "$DEVICE_BEFORE_DIR/KoboReader.sqlite" \
    "SELECT ContentID,Title,AnnotationsSyncToken,IsDownloaded FROM content WHERE ContentID='$PROBE';" \
    | tee "$ZZWB_RUN_DIR/$CYCLE_LABEL-pre-content.txt"
  sqlite3 -readonly "$DEVICE_BEFORE_DIR/KoboReader.sqlite" \
    "SELECT AnnotationsSyncToken FROM content WHERE ContentID='$PROBE';" \
    >"$PRE_ETAG"
  sqlite3 -readonly "$DEVICE_BEFORE_DIR/KoboReader.sqlite" \
    "SELECT COUNT(*) FROM Bookmark WHERE VolumeID LIKE '$PROBE%';" \
    | tee "$ZZWB_RUN_DIR/$CYCLE_LABEL-pre-bookmark-count.txt"
  python3 -c '
from pathlib import Path
import sys
p=Path(sys.argv[1])
lines=p.read_text().splitlines()
assert len(lines)==1 and lines[0].startswith("W/\""), lines
print({"cycle":sys.argv[2], "pre_etag_bytes":len(lines[0].encode("ascii"))})
' "$PRE_ETAG" "$CYCLE_LABEL"
  chmod 600 "$PRE_ETAG"
}
```

With the reader offline, set `CYCLE_LABEL` and call `snapshot_probe` immediately before preparing
that cycle's stage files. Do this separately for `cycle-r-cwng`, `cycle-r-zero`, and `cycle-b`.
Do not allow a Kobo GET or another sync between `snapshot_probe` and the cycle: the live composite
token can change on that GET. Each `*-pre-etag.txt` is valid only for the cycle named in its file.

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

It opens SQLite with `mode=ro` and `PRAGMA query_only=ON`, selects only ContentId
`053742ff-9094-43b2-8511-c0763c90ffab`, requires that it resolve to Calibre book 540, verifies
captured-object and raw-location digests, rejects non-captured provenance, and refuses an empty set
unless `--allow-empty` is explicit.

## 3. Prove the hot-copy source and deployed image baseline

The source of the hot-copy is exactly `cps/readingservices.py` in this worktree after the
`origin/main` integration. Record the configured image and image-shipped file hash before
overwriting it:

```bash
docker inspect --format '{{.Config.Image}}' "$ZZWB_CONTAINER" \
  | tee "$ZZWB_RUN_DIR/container-image.txt"
test "$(cat "$ZZWB_RUN_DIR/container-image.txt")" = \
  'ghcr.io/new-usemame/calibre-web-nextgen:dev'
docker inspect --format '{{.Image}}' "$ZZWB_CONTAINER" \
  >"$ZZWB_RUN_DIR/container-image-id.txt"
docker exec "$ZZWB_CONTAINER" sha256sum \
  /app/calibre-web-automated/cps/readingservices.py \
>"$ZZWB_RUN_DIR/image-readingservices.sha256"
rg -q '^be645af98cfdb180[0-9a-f]{48}  /app/calibre-web-automated/cps/readingservices\.py$' \
  "$ZZWB_RUN_DIR/image-readingservices.sha256"

docker cp cps/readingservices.py \
  "$ZZWB_CONTAINER:/app/calibre-web-automated/cps/readingservices.py"
docker exec "$ZZWB_CONTAINER" \
  python3 -m py_compile /app/calibre-web-automated/cps/readingservices.py
docker restart "$ZZWB_CONTAINER"
```

The prefix assertion is a deployment sanity check, while the complete hash file is the baseline
and final restore oracle. The `:dev` tag can move and must not replace either check. Wait for the
ordinary health check to report healthy. Exchange capture is already on in this container; do not
add or change its environment gate. Then create the private stage directory and prove it is
disarmed:

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

Use the unique label already assigned for the leg (`cycle-r-cwng`, `cycle-r-zero`, `cycle-b`, or
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
2. Open `The Heat Will Kill You First`, close it fully, and reopen it.
3. Tap **Sync now** again without altering the stage.
4. Close/reopen once more and tap **Sync now** a third time.

The pilot can open the exact title when Nickel is at Home:

```bash
"$KOBO_PILOT" open-book "$PROBE_TITLE" --expect-book-title "$PROBE_TITLE"
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
CYCLE_LABEL=cycle-r-cwng
snapshot_probe
cp "$ZZWB_RUN_DIR/empty-payload.json" "$ZZWB_RUN_DIR/payload.json"
DIGEST_PREFIX=$(shasum -a 256 "$ZZWB_RUN_DIR/payload.json" | awk '{print substr($1,1,16)}')
printf 'W/"CWNG:night-r-20260828:91:%s"\n' "$DIGEST_PREFIX" \
  >"$ZZWB_RUN_DIR/etag.txt"
cp "$ZZWB_RUN_DIR/etag.txt" "$ZZWB_RUN_DIR/cycle-r-cwng-etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt" \
  "$ZZWB_RUN_DIR/cycle-r-cwng-etag.txt"
```

Repeat sections 4 and 5. Pull `device-after-r-cwng`, require zero probe Bookmark rows, and require a
later captured `checkforchanges` request to echo `cycle-r-cwng-etag.txt` byte-for-byte. An echoed
tag proves Nickel can overwrite its previous stored token; absence proves nothing unless capture
also shows the staged GET completed.

### R2: the empty-set token `W/"0"`

Keep the identical empty body and change only the tag:

```bash
CYCLE_LABEL=cycle-r-zero
snapshot_probe
cp "$ZZWB_RUN_DIR/empty-payload.json" "$ZZWB_RUN_DIR/payload.json"
printf 'W/"0"\n' >"$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
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

Create `server-highlight.json` from **this probe's own captured create PATCH** in
`/config/.cwng-private-observability/kobo-reading-services/`. Select a PATCH capture whose exact
ContentId is `053742ff-9094-43b2-8511-c0763c90ffab`, then copy its `chapterFilename`, `startPath`,
`endPath`, `startChar`, and `endChar` position values. Do not use positions from a retired ZZ book,
another title, or a reconstructed KEPUB. Preserve the capture with the run evidence.

The object must use the successful Kobo GET highlight shape: exactly `attachments`,
`clientLastModifiedUtc`, top-level `context`, `highlightColor`, `highlightedText`, `id`, `location`,
and `type`; `location` contains exactly one `span` with `chapterFilename`, `chapterProgress`,
`startPath`, `endPath`, `startChar`, and `endChar`. `attachments` is `{}`, `type` is `highlight`,
`id` is a new canonical lower-case UUID, `clientLastModifiedUtc` is UTC RFC 3339 ending in `Z`, and
`highlightColor` is one of the five measured Kobo wire hex values. Do not use placeholder
chapter/path/text values: review the spec against that same probe create PATCH and a captured Kobo
GET before staging it.

The builder validates the exact field set, span shape, range, empty attachments, and measured color
palette, and emits exactly one annotation:

```bash
CYCLE_LABEL=cycle-b
snapshot_probe
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
```

Repeat sections 4 and 5. The first request should declare the token left by Cycle R, the GET must
serve the one-row payload and Cycle B ETag, and later requests should echo Cycle B exactly. Pull a
fresh device database and compare the staged `id`, text-free location fields, and color to the
probe's Bookmark row. Then open the exact chapter/page and **visually observe whether Nickel draws
the highlight**; a database row alone does not establish rendering.

This is **[ASSUMED SAFE ONLY FOR THIS DISPOSABLE PROBE]** and is not authority evidence for any
other book, firmware, or device.

## 8. Cycle B cleanup — return the probe to its empty baseline set

Put the reader offline. Stage the empty body and the exact live composite token recorded by
`snapshot_probe` immediately before Cycle B in `cycle-b-pre-etag.txt`; do not reuse either Cycle R
token and do not substitute `W/"0"` regardless of Cycle R's outcome:

```bash
cp "$ZZWB_RUN_DIR/empty-payload.json" "$ZZWB_RUN_DIR/payload.json"
cp "$ZZWB_RUN_DIR/cycle-b-pre-etag.txt" "$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
CYCLE_LABEL=cycle-b-cleanup
```

Repeat sections 4 and 5, then sync/close/open twice. Do not remove the rig until a fresh read-only
snapshot proves:

```text
Bookmark count for the probe = cycle-b-pre-bookmark-count.txt
AnnotationsSyncToken for the exact probe = the exact cycle-b-pre-etag.txt bytes
later checkforchanges request for the exact probe = the exact cycle-b-pre-etag.txt bytes
```

The changed-set cleanup requirement is Bookmark-set equality. Exact token restoration is an
additional byte-for-byte check and must use this book's own token captured immediately before
Cycle B. Complete and preserve that comparison before allowing a genuine Kobo annotations GET,
because such a GET advances the live composite manifest again.

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
docker compose up -d --force-recreate --no-deps "$ZZWB_SERVICE"
```

Verify the replacement has no scratch marker or arm, is healthy, is configured from the expected
mutable tag, and contains the exact bytes recorded before the hot-copy:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  ! grep -q ZZWB_EXPERIMENT_UUID /app/calibre-web-automated/cps/readingservices.py
  test ! -e /config/zzwb/ARMED
'
docker inspect --format '{{.Config.Image}} {{.State.Health.Status}}' "$ZZWB_CONTAINER" \
  | tee "$ZZWB_RUN_DIR/restored-container.txt"
test "$(cat "$ZZWB_RUN_DIR/restored-container.txt")" = \
  'ghcr.io/new-usemame/calibre-web-nextgen:dev healthy'
docker exec "$ZZWB_CONTAINER" sha256sum \
  /app/calibre-web-automated/cps/readingservices.py \
  >"$ZZWB_RUN_DIR/restored-readingservices.sha256"
cmp "$ZZWB_RUN_DIR/image-readingservices.sha256" \
  "$ZZWB_RUN_DIR/restored-readingservices.sha256"
```

If `/app/calibre-web-automated` is bind-mounted, recreation cannot discard the hot-copy. Restore the
bind from the exact local image ID recorded in `container-image-id.txt`, restart, and require the
same full-file `cmp` before putting the reader online. Do not identify the baseline by the mutable
`:dev` tag alone.

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
