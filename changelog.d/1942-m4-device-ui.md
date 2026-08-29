### Added

- **Every registered Kobo, KOReader, and browser now has its own data page.**
  The page separates highlights, standalone notes, dog-ears, reading positions,
  and the device's reported library, with an explicit switch between origin and
  current assignment.
- **Administrators can inspect the cross-account device fleet.** The board shows
  privacy-safe device metadata, per-class annotation counts, and Kobo authority
  coverage without exposing installation identifiers or identity fingerprints.

### Security

- **Device-scoped annotation and position reads now re-check the device owner's
  current filtered library at response time.** A later account-library or
  content restriction therefore removes the affected book from device and admin
  views instead of relying on older sync or queue state.
- **Inventory, removal counts, restore counts, and named deletion requests use
  that same live owner view and fail closed when its owner is unavailable.**
  Unmatched or newly excluded inventory rows cannot be exposed as device data or
  queued for deletion through these endpoints.

### Changed

- **Every device collection is now server-paged with a capped limit and an exact
  total.** The administrator board computes annotations, positions, authority,
  seed coverage, inventory, and storage in a fixed set of grouped SQL queries;
  its query count no longer grows with the number of users, books, or devices.
- **Inactive Kobo devices report zero current seeded and unseeded books.** Mixed
  seed coverage remains calculated and surfaced across active Kobo devices,
  rather than presenting retired devices as currently unseeded.
