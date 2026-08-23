# KOReader highlight-safety proof

Branch: `fix/plugin-highlight-safety-trio`

This is the executed evidence record for the two parked fixes. Every causal
link is labelled `OBSERVED` or `ASSUMED`; no assumed link is described as
verified. The branch remains parked for the next release cut.

## Deliverable 1 — continuation wire shape

Verdict: **OBSERVED SAFE**. A continuation request whose Lua empty table is a
JSON object does not assert that the device deleted its full live set. The
server normalizes `{}` to `[]`, and deletion authority remains exclusively the
IDs named in `deleted`.

The behavioral proof sends two routed PUT requests through
`/kosync/syncs/annotations` and production `apply_push`: the first carries 200
named deletes plus the surviving live annotation, and the continuation carries
`annotations: {}` plus two more named deletes. It asserts both response counts
and that the omitted, non-named live row survives.

### Executed wire result

Command:

```text
pytest -q \
  tests/unit/test_koreader_annotation_delete_reap.py::test_wire_delete_continuation_object_only_deletes_named_ids \
  tests/unit/test_koreader_annotation_delete_reap.py::test_wire_deleting_the_last_highlight_syncs \
  tests/unit/test_koreader_annotation_delete_reap.py::test_wire_malformed_annotations_is_rejected \
  tests/unit/test_cwasync_plugin_wire_contract.py
```

Output:

```text
collected 23 items
tests/unit/test_koreader_annotation_delete_reap.py ....       [ 17%]
tests/unit/test_cwasync_plugin_wire_contract.py ...sss.......sss... [100%]
17 passed, 6 skipped in 1.21s
```

OBSERVED assertions from the routed continuation test:

- first request: HTTP 200 and `deleted == 200`;
- continuation with JSON `annotations: {}`: HTTP 200 and `deleted == 2`;
- final live set: exactly `{"must-survive"}`;
- null and missing `annotations` remain HTTP 400 `invalid_annotations`.

### Detection mutation

Mutation point: keep the route predicate present but make its normalization
branch constant-false:

```python
if False and annotations == {}:
    annotations = []
```

Command:

```text
pytest -q tests/unit/test_koreader_annotation_delete_reap.py::test_wire_delete_continuation_object_only_deletes_named_ids
```

Output:

```text
collected 1 item
tests/unit/test_koreader_annotation_delete_reap.py F
E AssertionError: {'error': 'invalid_annotations',
                    'message': 'annotations must be an array'}
E assert 400 == 200
1 failed in 1.03s
```

Restored output: `1 passed in 1.00s`.

This is a semantic detection, not a new-call-signature failure: the mutated
server receives the same routed request and rejects the continuation's actual
JSON shape. OBSERVED server semantics after restoration are therefore: `{}` is
accepted solely as an empty annotation list; because `apply_push` only acts on
portable rows present in that list and IDs explicitly named in `deleted`, it
cannot interpret the continuation as permission to wipe the device's omitted
live set.

## Deliverable 2 — digest-bound provider read

Verdict: **OBSERVED SAFE** at the executable provider/planner boundary.

New focused suite: `digest_binding_test.lua`. It executes the production
singleton provider and orders the positive case first so an always-refuse guard
cannot masquerade as safety. It proves three behaviors:

1. a normal single-book read whose expected/current digests are equal returns
   its real annotation list;
2. after the singleton moves to another digest, the stale callback gets `nil`;
3. a caller omitting the optional expected digest retains the old readable
   contract rather than being refused.

The existing `sync_logic_test.lua` then proves a mismatch becomes
`known=false`, `deletions={}`, and `may_save_watermark=false`; the Python source
gate pins the two wiring legs that pass `digest` from `main.lua` through the
planner to the delayed provider read.

### Green execution

Command:

```text
pytest -q --tb=short tests/unit/test_920_koreader_local_set_read_authority.py
```

Output after restoration:

```text
collected 7 items
tests/unit/test_920_koreader_local_set_read_authority.py ....... [100%]
7 passed in 0.15s
```

### Red-first and predicate mutations

All mutations keep the relevant call/conditional present. No failure is an
`unexpected keyword argument` or other new-signature artifact; every failure
names the incorrect runtime behavior.

Mutation point D2-P1 is the provider predicate.

Constant-false (the pre-fix behavior: never reject a replaced context):

```lua
if false then return nil end
```

Output: `2 failed, 5 passed in 0.17s`. Both failing executable suites report
that a stale callback returned a table instead of `nil`:
`device_annotations_test.lua` and `digest_binding_test.lua`. This is the RED
regression observation for c4847da49.

Constant-true (the over-strict mutation: reject every context):

```lua
if true then return nil end
```

Output: `2 failed, 5 passed in 0.16s`. The dedicated suite fails first at
`a normal single-book sync accepts its own digest` (`table` expected, `nil`
actual); the pre-existing provider suite independently fails its readable
attached-reader case.

Mutation point D2-W1 drops the digest between planner and provider while
leaving the parameter/call present (`resolveLocalSet(..., nil)`). Output:
`1 failed, 6 passed in 0.15s`; `sync_logic_test.lua` reports the matching
context as unreadable (`true` expected, `false` actual).

Mutation point D2-W2 drops the digest at the `main.lua` call while leaving the
fourth argument present (`planLocalContribution(..., nil)`). Output:
`1 failed, 6 passed in 0.15s`; the call-site wiring gate reports that the
expected document digest no longer reaches the delayed read.

Mutation counts at D2-P1 are therefore **constant-false: 2 failures** and
**constant-true: 2 failures**. The two wiring points each add one independent
detection failure when degraded.

## Deliverable 3 — bounded delete requests

Verdict: **OBSERVED SAFE** at the executable client/callback boundary.

`sync_client_outcome_test.lua` now pins the complete request-size boundary:

- zero deletes preserves the legacy single annotation request and sends no
  `deleted` authority;
- 200 IDs makes one request;
- 201 IDs makes 200 + 1;
- 451 IDs makes 200 + 200 + 51;
- ordering is preserved, the live annotation table is sent exactly once, and
  the logical callback completes exactly once;
- a 503 on chunk two of three stops before chunk three and returns one failed
  logical outcome.

### Green execution

Command:

```text
pytest -q --tb=short tests/unit/test_920_koreader_local_set_read_authority.py
```

Restored output:

```text
collected 7 items
tests/unit/test_920_koreader_local_set_read_authority.py ....... [100%]
7 passed in 0.14s
```

### Red-first observation

The pre-fix unbounded behavior was restored without removing the loop by
setting `ANNOTATION_DELETE_CHUNK_SIZE = math.huge`. Output:
`1 failed, 6 passed in 0.16s`; the Lua suite reports `201 delete ids cross the
boundary exactly once`, expected 2 requests, actual 1. This is the RED
regression observation for d5f37bfc6, and it fails on request behavior rather
than a changed function signature.

### Predicate mutation matrix

Every row was run with the same command above. Each `1 failed` is the
`sync_client_outcome_test.lua` pytest parameter; the other six focused tests
remain green.

| Point | Constant-true result | Constant-false result | Detected behavior |
|---|---:|---:|---|
| D3-P0 delete-presence (`has_deletes`) | 1 failed, 6 passed | 1 failed, 6 passed | true drops the zero-delete request (expected 1, actual 0); false omits the real delete list (`deleted` is nil) |
| D3-P1 first-chunk live-set selector (`first == 1`) | 1 failed, 6 passed | 1 failed, 6 passed | true replays the live set on the continuation (expected 0 rows, actual 1); false omits it from the first request |
| D3-P2 chunk-failure predicate (`not ok or status != 200`) | 1 failed, 6 passed | 1 failed, 6 passed | true stops a 201-ID success after request one; false sends request three after chunk two returned 503 |

Observed runtimes were 0.15–0.16 seconds per mutation. The D3-P2
constant-false test also retains assertions that the one logical callback is
failure with reason `HTTP 503`; once the stop-count assertion is restored, the
green run reaches and passes those watermark-facing outcome assertions.

## Deliverable 4 — over-strictness report

Focused command:

```text
pytest -q --tb=short \
  tests/unit/test_920_koreader_local_set_read_authority.py \
  tests/unit/test_920_koreader_push_only_authority.py::test_already_hidden_row_is_not_deleted_again \
  tests/unit/test_koreader_annotation_delete_reap.py::test_wire_delete_continuation_object_only_deletes_named_ids
```

Output:

```text
collected 9 items
tests/unit/test_920_koreader_local_set_read_authority.py ....... [ 77%]
tests/unit/test_920_koreader_push_only_authority.py .            [ 88%]
tests/unit/test_koreader_annotation_delete_reap.py .             [100%]
9 passed in 1.04s
```

### c4847da49 — digest binding

**What is newly refused?**

- **OBSERVED:** Previous code accepted whatever live annotation collection the
  module singleton exposed when the pull callback finally ran. New code returns
  `nil` only when the caller supplied an expected digest and the singleton now
  holds a different digest.
- **OBSERVED:** It does not refuse a matching digest. The dedicated Lua suite
  gets one real portable annotation for `digest-current`.
- **OBSERVED:** It does not refuse an omitted expected digest. The dedicated
  suite calls `readAll(nil, nil)` after a context replacement and receives the
  current real list, preserving interface compatibility outside the bound call.

**Can the refusal be wrong on a legitimate sync?**

- **OBSERVED:** Two legitimate overlapping sync actions for different books can
  trigger it. The older callback is still a legitimate callback, but the book-B
  collection it can now see is not legitimate evidence about book A. Refusing
  that local contribution is therefore the required outcome, not an erroneous
  refusal.
- **OBSERVED:** Replacing the reader context while reopening/syncing the same
  book retains the same digest and passes equality; this guard is document
  identity, not a per-sync generation lock, so it does not reject same-book
  overlap merely because the singleton object was replaced.
- **OBSERVED:** If the active file changes enough to produce a different digest
  during the round trip, the callback is refused. Its request is still bound to
  the old server document key, so contributing the newly digested context would
  be cross-document data. The refusal is correct.
- **ASSUMED:** The existing partial-MD5 digest has the collision resistance the
  rest of kosync already assumes. A collision would make this guard too
  permissive, not too strict, and is outside the behavioral delta of this fix.

**Does it degrade safely rather than refusing annotation sync outright?**

The traced production path is:

```text
pull succeeds
  -> planLocalContribution(expected digest)
     -> mismatch: list={}, known=false, deletions={}, may_save_watermark=false
  -> diffAnnotations({}, remote) still runs
  -> push_all_local forces send_to_server={}
  -> apply_to_device remains available for addressable remote rows
  -> no local push/deletes; no watermark replacement
```

- **OBSERVED (executable):** The mismatch planner returns zero deletions and
  `may_save_watermark=false`.
- **OBSERVED (executable):** Passing its placeholder list to the normal diff
  still yields one `apply_to_device` remote row, while the phase-1
  `push_all_local` override yields zero `send_to_server` rows.
- **OBSERVED (source-pinned):** `main.lua` contains no `plan.known` branch, so
  unknown local authority does not return/abort after a successful pull.
- **OBSERVED (source-pinned):** Actual watermark persistence is guarded by
  `if ok2 and plan.may_save_watermark`; a mismatch cannot satisfy the latter.
- **OBSERVED:** Progress sync is a separate path and neither the digest
  predicate nor its return value is threaded into progress GET/PUT. This fix
  changes only `syncAnnotations` local contribution.

**User-visible change:**

- **OBSERVED:** In the narrow race where the active book changes during the
  annotation pull, the older cycle omits device-to-server highlight changes and
  named deletes instead of applying the new book's collection to the old book.
  Those changes wait for the next sync in the correct document context.
- **OBSERVED:** A normal single-book cycle has no visible behavior change; its
  digest passes and its real local set contributes normally.
- **OBSERVED:** An interactive mismatched cycle may report only the device-side
  result (often zero) because there is deliberately no server contribution. It
  does not display a new hard error or stop progress sync.

**Over-strictness verdict:** no wrongful refusal found. The guard is narrowly
document-scoped, explicitly permits the legacy nil expectation and same-digest
contexts, and its refusal removes only authority the callback no longer owns.

### d5f37bfc6 — delete chunking

**What is newly refused or omitted?**

- **OBSERVED:** A delete list over 200 IDs is no longer accepted as one client
  request. It is split into requests of at most 200 IDs.
- **OBSERVED:** Continuation requests omit the complete live annotation list;
  it is sent exactly once on the first chunk. Continuations carry an empty JSON
  collection plus their named deletes.
- **OBSERVED:** After the first failed/raised/non-200 chunk, later chunks are
  not attempted in that logical push.
- **OBSERVED:** Zero-delete pushes and lists of exactly 200 or fewer retain one
  request. The zero-delete request retains all annotations and acquires no
  `deleted`/`delete_source` fields.

**Can those refusals be wrong on a legitimate sync?**

- **OBSERVED:** Omitting repeated live annotations is valid because server
  deletion authority is exclusively the per-request `deleted` list; the routed
  continuation test proves that an omitted, non-named live row survives.
- **OBSERVED:** Stopping after a failed chunk is necessary to keep the logical
  result honest. Continuing could report success from a later response while a
  middle range never reached the server.
- **OBSERVED:** Re-sending the whole logical delete list after a partial failure
  is safe. The server test `test_already_hidden_row_is_not_deleted_again`
  reports `deleted == 0` for an already-hidden ID, so completed early chunks
  are idempotent on retry and do not repeat delete fan-out.
- **OBSERVED:** A permanently malformed chunk could continue to block later
  chunks on every retry, but the old single unbounded request would also reject
  the entire list. Client-generated watermark IDs are non-empty strings, the
  server's accepted member shape, so no new legitimate-ID refusal was found.

**Can a partial failure advance the watermark?**

- **OBSERVED (executable):** Responses `200, 503, 200` produce exactly two
  requests, exactly one callback, `ok=false`, reason `HTTP 503`, and leave a
  simulated caller's `watermark_saved=false`.
- **OBSERVED (source-pinned):** Production `main.lua` calls
  `saveAnnotationWatermark(localList)` only inside
  `if ok2 and plan.may_save_watermark`. The combined client callback is invoked
  once and cannot provide `ok2=true` until every chunk returned 200.
- **OBSERVED:** Therefore earlier server tombstones may exist after a partial
  failure, but the client does not treat the delete set as complete. The next
  sync re-derives/retries it; already-hidden IDs are server no-ops.

**User-visible change:**

- **OBSERVED:** A deletion of more than 200 highlights makes multiple bounded
  HTTP requests and can take slightly longer from additional round trips.
- **OBSERVED:** If a later chunk fails, the user sees the existing server-push
  failure with its reason, some early named deletions may already be visible on
  the server, and the rest retry on a later sync. Previously the single large
  request either completed or failed as one transport operation.
- **OBSERVED:** Deletes of 200 or fewer and all no-delete annotation pushes are
  behaviorally unchanged.

**Over-strictness verdict:** no wrongful refusal found. The only newly omitted
work is redundant live-set replay, and the only newly stopped work is the
unattempted suffix after a logical failure; both are pinned to preserve retry
authority and user-data safety.
