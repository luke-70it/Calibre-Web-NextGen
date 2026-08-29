### Added

- **Reading positions are now retained per Kobo and browser device.** A
  re-downloaded Kobo book receives its resolved reading state on the next sync
  even when the normal reading-state cursor is already ahead, while
  byte-identical entitlement replays remain suppressed without re-arming the
  repair.

### Fixed

- **A fresh-download cover reset can no longer overwrite a real cross-device
  position.** Device observations remain independently inspectable, resolved
  progress keeps the furthest position, and status and reading statistics use
  the newest valid device timestamp.
