### Added

- **Owned Kobo annotations can become safely server-authoritative without a
  manual database edit.** CWNG captures the complete upstream annotation set
  per active Kobo, preserves its exact pages, and keeps unsafe or oversized
  sets proxied instead of serving a destructive subset.

### Fixed

- **Server-authoritative Kobo books no longer fall back to a stale cloud
  replacement set.** Seed promotion now proves captured annotation IDs, keeps
  newer server edits and tombstones, serializes reconciliation per book,
  expires abandoned captures, isolates later-device failures, and provides an
  authenticated retry for an initial quarantined seed.
