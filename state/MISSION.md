# Mission: Make the SPA default and persist Discover visibility per user

Updated: 2026-08-28  Phase: frontend build and E2E  Status: 3/8 outcomes observed

## Definition of done

- [x] Required project authority, code index, stale M3 briefing, capability/routing, git-manager, run-to-done, and accessibility guidance consumed.
- [ ] Cookie-less browser navigation to `/` and GET `/login` selects the SPA; old `cwng_prefer_spa=1` remains SPA-compatible.
- [ ] Classic opt-out sticks across requests/restarts and repeated classic↔SPA round trips; feedback popup remains one-shot and the classic nudge is removed.
- [ ] Machine-client requests (missing or wildcard Accept, curl/wget, OPDS UA, Kobo routes) have unchanged status/redirect behavior; explicit browser navigation and reverse-proxy subpaths work.
- [x] Generic named per-user JSON preference API/client facility exists without a migration and safely validates/rolls back.
- [ ] Discover preference is server-authoritative for authenticated users, adopts an existing local hidden value once, works across browsers/logout/localStorage clearing, and stays local-only for anonymous users.
- [ ] Objective-specific tests are demonstrated red on the origin/main base and green on the branch; touched Python suites, frontend build/typecheck, and touched e2e/a11y specs pass.
- [ ] Live local HTTP/browser flow, adjacent regression pass, changelog fragment, commits, and pushed final HEAD are recorded with OBSERVED/ASSUMED evidence.

## Now / next action

Install the locked frontend toolchain, run the production build, add multi-context/adoption/guest browser coverage, then exercise the local container.

## How to build/run/verify

- `python -m pytest <touched unit suites>`
- `cd frontend && npm run build`
- `cd frontend && npm run test:e2e -- <touched specs>`
- Local Docker/dev HTTP and browser verification using the repository harness.

## Decisions & rationale

- 2026-08-28: implement inline as Sol; no delegate fleet, matching current model-routing doctrine and the operator's implementer assignment.
- 2026-08-28: reuse `User.view_settings`; no schema migration.
- 2026-08-28: build a generic named boolean-preference facility but wire only Discover in this pass.
- 2026-08-28: remove the classic opt-in nudge and retain only the plain new-UI nav affordance plus one-shot departure feedback.
- 2026-08-28: M3 briefing is stale since 2026-06-12; treat it as historical and rely on the operator-supplied branch/objectives plus current code.
- 2026-08-28: use `cwng_prefer_classic=1` as the opt-out; continue stamping/deleting legacy `cwng_prefer_spa` for downgrade compatibility.
- 2026-08-28: browser routing requires an explicit positive `text/html` media range and rejects stated non-document/non-navigation Fetch Metadata.
- 2026-08-28: `/me.preferences` returns bool-or-null; `null` uniquely means eligible for one-time localStorage adoption.
- 2026-08-28: mutations use one allowlisted boolean map, one endpoint-owned transaction, optimistic `/me` cache writes, serialized requests, and rollback.

## Open questions for the operator

- None; the supplied defaults and scope are sufficient.
