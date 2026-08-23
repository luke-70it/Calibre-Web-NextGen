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
