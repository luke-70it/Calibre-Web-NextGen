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
