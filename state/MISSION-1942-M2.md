# Mission: #1942 M2 annotation seeding pipeline

Updated: 2026-08-29
Phase: complete
Status: 8/8 outcomes done and verified

## Definition of done

- [x] Additive, idempotent migration adds `ever_authoritative` and `seed_kind` before ORM use; migration tests pass.
- [x] An eligible owned annotations GET creates a pending capture, proxies Kobo, persists every captured page, reconciles the final response, and promotes only a complete servable set.
- [x] Promotion repairs legacy content IDs, preserves/mints generation IDs correctly, increments authority revision, records accepted capture counts, and sets sticky `ever_authoritative`.
- [x] Unsafe captures quarantine with enumerated privacy-safe reason codes for local-below-upstream, local-over-100, or multi-page capture; no subset becomes locally serveable.
- [x] Once ever authoritative, owned PATCH persists and returns the byte-exact local 204 acknowledgement even when current gates fail.
- [x] Mixed-device state is explicit: accepted captures are assessed against all active Kobo devices, missing devices are captured on their next eligible GET, and a partial-seed diagnostic is emitted.
- [x] New `tests/unit/test_1942_seed_pipeline.py`, existing #1923 regression tests, and the full unit suite report exact observed counts, with known sandbox-infrastructure failures separated.
- [x] Changelog fragment is present; scoped changes are committed with the `new-usemame` identity and a verified bundle is produced.

## Now / next action

Mission complete. Hardware verification remains the post-merge Clara step owned by the operator's later workflow.

## Verification commands

- Focus: project test command discovered from existing test documentation/configuration, limited to `tests/unit/test_1942_seed_pipeline.py` and `tests/unit/test_1923_owned_annotations_local_authority.py`.
- Migration: run the repository's existing migration test path against fresh and pre-column schemas, including an idempotent second pass.
- Full unit suite: run the repository's normal full unit command; record passed/failed/error/skipped counts and classify only evidenced sandbox-infrastructure failures separately.
- Git: `git diff --check`; inspect scoped diff; verify commit author and remote owner before push/bundle.

## Decisions and rationale

- 2026-08-29: preserved unrelated completed `state/MISSION.md`; this mission uses a task-specific ledger.
- 2026-08-29: capture all upstream pages, but quarantine any capture requiring more than the one page local authority can serve.
- 2026-08-29: implement the spec's sticky design: `ever_authoritative=1` permanently prevents later owned PATCH forwarding.
- 2026-08-29: hardware Clara verification is deliberately deferred until after merge, per operator scope.
- 2026-08-29: Git Manager hydration unavailable because the secrets broker is down; no credential fallback attempted.
- 2026-08-29: the all-devices calculation intentionally means active `kind='kobo'` devices. Browser devices cannot originate the Kobo annotations GET that supplies a capture; this matches the M2 instruction to capture each other active Kobo on its next GET.
- 2026-08-29: no M3-M5 or entitlement-replay code was changed; hardware verification remains post-merge.
- 2026-08-29: implementation committed as `ae36f43bac2ce11fc3b4a210fefe6ed157e1df4c` with the exact `new-usemame` identity. Push was attempted and blocked by managed DNS (`github.com` could not resolve), so delivery uses a verified complete-history bundle. The sandbox cannot write the requested parent-directory spelling; the final artifact is `1942-m2.bundle` in this writable worktree root.

## Evidence classification

- OBSERVED: operator supplied the M2 objective, exact scope, done conditions, identity, and hardware deferral.
- OBSERVED: required spec and seeding runbook were read; their M2, migration, completeness, C1-C4, wire, and replay constraints govern this mission.
- OBSERVED: branch starts clean as `feat/1942-m2-seeding...origin/main` with M1 merged.
- OBSERVED: M3 briefing is stale (2026-06-12) and records zero unresolved escalations at its last update.
- OBSERVED: focused final verification passed 27/27 (`test_1942_seed_pipeline.py`, #1923, and raw materialization regression).
- OBSERVED: adjacent schema/route/persistence verification passed 98/98 before the final focused run; production migration replay and classifier follow-ups passed 3/3.
- OBSERVED: final full unit suite: 7,479 passed, 106 skipped, 17 failed in 222.84s. All 17 failures are sandbox infrastructure: 10 loopback socket-bind denials and 7 `ps` execution denials; no application assertion remains failed.
- OBSERVED: Python compilation, focused Ruff on the new service/tests and adjacent small modules, and `git diff --check` pass.
- ASSUMED: no additional private Git Manager directive conflicts with the direct operator brief; hydration was unavailable because the secrets broker was not running.
