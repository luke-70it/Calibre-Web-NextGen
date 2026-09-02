### Added

- **Choose a private cover without changing the shared library.** Each user can
  upload a cover, paste a cover URL, or choose one from the cover picker. The
  personal cover appears only in that user's views and e-reader deliveries;
  administrators still control the library cover seen by everyone else.

### Fixed

- **Cover writes are now crash-safe.** New global and personal covers are
  validated in a temporary sibling and published atomically only after their
  metadata transaction succeeds, preserving the previous cover on failure.
