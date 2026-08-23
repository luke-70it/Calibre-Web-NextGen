# ZZWB arbitrary-ETag hardware experiment — run, observe, and revert

> **SCRATCH INSTRUMENT — NEVER MERGE.** This procedure is only for `ZZWB Writeback Probe`,
> ContentId `d83c9bfd-91e1-4bed-a1a6-9c50d15ae46c`. The commands below deliberately hot-copy a
> scratch file into one running container. They are not a deployment design.

## 1. Safety boundary and what counts as an answer

- **[OBSERVED]** The probe currently has zero device annotations, is disposable, and had the
  empty-set token `W/"0"` in the August 23 snapshot.
- **[OBSERVED]** A named-book annotations GET that returned 304, 503, or hung caused Nickel to
  replace that book's local set with empty. Therefore this rig names the probe only when all three
  files exist and validate: `/config/zzwb/ARMED`, `payload.json`, and `etag.txt`.
- **[OBSERVED IN TESTS]** A ready probe GET is local-only, ignores `If-None-Match`, and returns 200
  with the exact staged bytes and exact staged ETag. That code path performs no upstream request.
- **[OBSERVED IN TESTS]** A non-probe ContentId short-circuits before arming-directory access. With
  `ARMED` absent, probe and non-probe annotation GETs return the original proxy response object
  unchanged; checkforchanges does not name the probe.
- **[ASSUMED UNTIL THIS RUN]** Nickel treats the HTTP ETag as opaque and will declare the exact
  staged CWNG tag in later checkforchanges requests. The experiment succeeds only if the same
  exact string appears in at least two later request captures and the staged set remains adopted.

The recommended tag spelling is exactly:

```text
W/"CWNG:<generation-id>:<authority-revision>:<digest-prefix>"
```

Do not edit stage files while the reader can sync. Put the reader offline first, disarm, replace
both files atomically, arm last, and only then restore connectivity. This prevents a file-change
window from overlapping a Nickel GET.

## 2. Prepare locally; do not touch the running container yet

Run from the scratch repository root:

```bash
test "$(git branch --show-current)" = "scratch/zzwb-writeback-experiment"
test -z "$(git status --porcelain)"
pytest -q \
  tests/unit/test_zzwb_experiment.py \
  tests/unit/test_zzwb_envelope_builder.py \
  tests/unit/test_zzwb_runbook.py \
  tests/unit/test_kobo_annotation_stage0.py \
  tests/unit/test_1660_kobo_checkforchanges_filter.py \
  tests/unit/test_30d264_checkforchanges_containment.py
```

Create a private local results directory and define the one production container name used by the
checked-in compose file:

```bash
install -d -m 700 ./zzwb-run
ZZWB_CONTAINER=calibre-web-nextgen
ZZWB_RUN_DIR=$PWD/zzwb-run
ZZWB_DB_SNAPSHOT=/path/to/offline/app.db
```

`ZZWB_DB_SNAPSHOT` must be an offline copy, not the database opened by the running application.
The builder opens it with SQLite `mode=ro` plus `PRAGMA query_only=ON`.

To build a non-empty payload from the Stage 0 rows:

```bash
./scripts/zzwb_build_annotation_envelope.py \
  --database "$ZZWB_DB_SNAPSHOT" \
  --output "$ZZWB_RUN_DIR/payload.json" \
  --force
python3 -m json.tool "$ZZWB_RUN_DIR/payload.json" >/dev/null
```

**[OBSERVED IN TESTS]** The builder resolves only the fixed probe UUID, selects visible rows in
binary annotation-ID order, accepts only `kobo_patch`/`kobo_cloud_seed` provenance, checks the
stored object SHA-256, and uses Stage 0's lexical projector to require byte-identical stored
`location`. It concatenates the original `raw_annotation_json` BLOBs; it does not recreate their
fields. By default it refuses zero rows. `--allow-empty` permits an empty envelope only when the
snapshot actually has zero visible materializations.

For Cycle A, whose device-side starting set is measured empty, stage the measured empty body rather
than the non-empty materialization build:

```bash
python3 -c 'from pathlib import Path; Path("zzwb-run/payload.json").write_bytes(b"{\"annotations\":[],\"nextPageOffsetToken\":null}")'
DIGEST_PREFIX=$(shasum -a 256 "$ZZWB_RUN_DIR/payload.json" | awk '{print substr($1,1,16)}')
printf 'W/"CWNG:night-a:1:%s"\n' "$DIGEST_PREFIX" >"$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
```

Record the exact values; do not reconstruct them from memory later:

```bash
wc -c "$ZZWB_RUN_DIR/payload.json"
shasum -a 256 "$ZZWB_RUN_DIR/payload.json"
sed -n '1p' "$ZZWB_RUN_DIR/etag.txt"
```

## 3. Hot-copy the instrument and restart

These are the only application-code deployment commands for the experiment:

```bash
docker cp cps/readingservices.py \
  "$ZZWB_CONTAINER:/app/calibre-web-automated/cps/readingservices.py"
docker exec "$ZZWB_CONTAINER" \
  python3 -m py_compile /app/calibre-web-automated/cps/readingservices.py
docker restart "$ZZWB_CONTAINER"
```

Wait for the ordinary health check to report healthy. Before staging, prove the directory is not
armed:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  install -d -m 700 -o abc -g abc /config/zzwb
  rm -f /config/zzwb/ARMED
  test ! -e /config/zzwb/ARMED
'
```

## 4. Stage and arm one cycle

First disable the reader's Wi-Fi or otherwise keep it from syncing. Then run this exact order:

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

Verify the armed files from inside the container without printing annotation text:

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

## 5. Run Cycle A and read the exact result

Before re-enabling Wi-Fi, take a read-only device snapshot. Set `KOBO_PILOT` to the existing
project-root wrapper; it reads the database, WAL, and SHM and integrity-checks only the local copy:

```bash
KOBO_PILOT=/path/to/project-root/tools/kobo-pilot/kobo-pilot
"$KOBO_PILOT" pull-db --host 10.0.20.250 \
  --output "$ZZWB_RUN_DIR/device-before-a"
CYCLE_START=2026-08-23T00:00:00Z
```

Replace `CYCLE_START` with the actual UTC instant immediately before restoring Wi-Fi. Then:

1. Restore Wi-Fi and tap **Sync now** once.
2. Open `ZZWB Writeback Probe`, close it fully, and reopen it.
3. Tap **Sync now** again without changing either staged file.
4. Close/reopen once more and tap **Sync now** a third time.

`kobo-pilot` can open the exact title when the reader is at Home:

```bash
"$KOBO_PILOT" open-book "ZZWB Writeback Probe" \
  --host 10.0.20.250 --expect-book-title "ZZWB Writeback Probe"
```

Collect logs and extract the structured records:

```bash
docker logs --since "$CYCLE_START" "$ZZWB_CONTAINER" 2>&1 \
  | tee "$ZZWB_RUN_DIR/cycle-a.log"
sed -n 's/^.*ZZWB checkforchanges //p' "$ZZWB_RUN_DIR/cycle-a.log" \
  | jq -c . \
  | tee "$ZZWB_RUN_DIR/cycle-a-checkforchanges.jsonl"
rg 'ZZWB: serving staged annotations' "$ZZWB_RUN_DIR/cycle-a.log"
```

Each natural checkforchanges exchange produces four records:

- `request`: parsed count and every `(order, ContentId, etag)` pair;
- `forwarded`: the exact filtered array sent upstream, or an empty array;
- `upstream`: status, only `Content-Type`/`ETag`, and recognized IDs; arbitrary/unrecognized bodies
  are logged only as byte count plus SHA-256;
- `final`: exact status and bare ID array returned to Nickel.

**Success for 11.2** requires all of the following, each **[OBSERVED]** from the artifacts:

1. the first staged GET log reports 200-serving telemetry with Cycle A's body SHA and ETag;
2. the next two `request` records declare Cycle A's exact ETag byte-for-byte;
3. the probe remains at zero Bookmark rows after both close/reopen cycles; and
4. no non-probe ContentId appears in any `final` array solely because of this rig.

Take the post-cycle snapshot:

```bash
"$KOBO_PILOT" pull-db --host 10.0.20.250 \
  --output "$ZZWB_RUN_DIR/device-after-a"
sqlite3 -readonly "$ZZWB_RUN_DIR/device-after-a/KoboReader.sqlite" \
  "SELECT ContentID,AnnotationsSyncToken,IsDownloaded FROM content WHERE ContentID LIKE 'd83c9bfd-91e1-4bed-a1a6-9c50d15ae46c%';"
sqlite3 -readonly "$ZZWB_RUN_DIR/device-after-a/KoboReader.sqlite" \
  "SELECT COUNT(*) FROM Bookmark WHERE VolumeID LIKE 'd83c9bfd-91e1-4bed-a1a6-9c50d15ae46c%';"
```

The `AnnotationsSyncToken` query is corroboration only. **[OBSERVED]** Section 6d found the same
fourteen-entry manifest on unrelated books, so cross-book token comparison is not trustworthy.
The load-bearing echo evidence is the incoming `etag` for this exact ContentId in the captured
request body.

## 6. Optional Cycle B: advance the CWNG revision and serve captured rows

Only do this after Cycle A has echoed twice. Put the reader offline, disarm, then rebuild
`payload.json` from the offline Stage 0 snapshot using the command in section 2. Generate a new tag:

```bash
DIGEST_PREFIX=$(shasum -a 256 "$ZZWB_RUN_DIR/payload.json" | awk '{print substr($1,1,16)}')
printf 'W/"CWNG:night-b:2:%s"\n' "$DIGEST_PREFIX" >"$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
```

Repeat section 4 exactly, then restore Wi-Fi and run two sync/close/open cycles. The first request
should declare Cycle A's tag, the GET should serve Cycle B, and the later requests should declare
Cycle B's tag exactly. Pull a fresh read-only database and compare Bookmark ID/count/location rows
to the IDs in `payload.json`. This cycle is **[ASSUMED SAFE ONLY FOR THIS PROBE]** because the probe
is disposable and started with zero rows; it is not authority evidence for any other book.

## 7. Restore the probe's measured device state before removing the rig

The measured pre-run state was zero Bookmark rows plus `W/"0"`. If Cycle B introduced rows, stage
the measured empty body again and set the staged header to Kobo's measured empty token:

```bash
python3 -c 'from pathlib import Path; Path("zzwb-run/payload.json").write_bytes(b"{\"annotations\":[],\"nextPageOffsetToken\":null}")'
printf 'W/"0"\n' >"$ZZWB_RUN_DIR/etag.txt"
chmod 600 "$ZZWB_RUN_DIR/payload.json" "$ZZWB_RUN_DIR/etag.txt"
```

With the reader offline, repeat section 4, then restore Wi-Fi and sync twice. Do not proceed until a
fresh read-only device snapshot shows both:

```text
Bookmark count for the probe = 0
AnnotationsSyncToken for the probe = W/"0"
```

Also require the second cleanup checkforchanges request to declare `W/"0"`. **[OBSERVED if all
three agree]** this restores the probe's measured annotation set and token. It does not alter any
other book.

## 8. Disarm, preserve evidence, and fully remove the hot-copy

Keep the reader offline until the container has been recreated. First disarm and archive the stage:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c 'rm -f /config/zzwb/ARMED'
docker cp "$ZZWB_CONTAINER:/config/zzwb" "$ZZWB_RUN_DIR/stage-final"
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  rm -f /config/zzwb/payload.json /config/zzwb/etag.txt
  rm -f /config/zzwb/payload.json.next /config/zzwb/etag.txt.next
  rmdir /config/zzwb 2>/dev/null || true
'
```

The deleted container-side stage is recoverable from `$ZZWB_RUN_DIR/stage-final`. From the
directory containing the production compose file, recreate the service from its configured image;
this is the hard reset that discards the hot-copied application layer:

```bash
docker compose up -d --force-recreate --no-deps calibre-web-automated
```

Verify the replacement contains no scratch marker and no arming directory:

```bash
docker exec "$ZZWB_CONTAINER" sh -eu -c '
  ! grep -q ZZWB_EXPERIMENT_UUID /app/calibre-web-automated/cps/readingservices.py
  test ! -e /config/zzwb/ARMED
'
docker inspect --format '{{.State.Health.Status}}' "$ZZWB_CONTAINER"
```

If `/app/calibre-web-automated` is bind-mounted rather than image-backed, recreation cannot discard
the copied file; restore that bind from the deployed image/source before restarting. Under the
stated copy-into-container deployment shape, recreation is the complete code reset.

## 9. What this rig can and cannot tell us

### 11.2 — arbitrary ETag grammar

- **Can answer:** whether this Clara BW accepts a valid non-composite CWNG ETag on a 200 annotations
  GET and later declares the exact string for the same probe ContentId across repeated cycles.
- **Can answer with Cycle B:** whether it advances from one arbitrary CWNG revision tag to another
  while adopting a changed exact-Kobo-shape set.
- **Cannot answer:** other firmware/models, multi-device convergence, long-offline behavior, or
  whether arbitrary tags survive firmware/database repair operations not exercised tonight.

### 11.1 — batched checkforchanges

- **Can observe:** any natural request array Nickel actually sends—count, order, every incoming
  ETag, the exact forwarded sub-array, upstream result, and final result.
- **Cannot force:** this rig does not force Nickel to emit a multi-book batch. If tonight's requests
  remain single-book, 11.1 remains open.
- **Cannot validate production routing:** the mixed authoritative/unseeded/non-owned production algorithm remains untested
  unless the operator independently creates that exact natural batch. The scratch path only names
  the fixed probe and otherwise retains current ownership containment.

### 11.3 — empty set and deletion representation

- **Already known before this run [OBSERVED]:** the device's empty-set token is `W/"0"`.
- **Can corroborate:** Cycle A/cleanup can show an empty body under an arbitrary tag or `W/"0"`,
  plus the next incoming declaration for this probe.
- **Cannot derive:** this rig does not derive Kobo's deletion-manifest representation, stable-ID
  hash, or version transition. It also does not by itself execute/capture the complete native
  create → sync → delete → sync manifest sequence required to close the remaining half of 11.3.
