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
