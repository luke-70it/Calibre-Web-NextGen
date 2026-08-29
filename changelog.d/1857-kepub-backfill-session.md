### Fixed

- **One failed Kobo KEPUB conversion no longer prevents every later synced book
  from being converted.** The startup backfill now rolls back and replaces a
  failed database session between books, stops after three failed recovery
  attempts instead of flooding the log for the rest of the library, and reports
  exactly how many books it processed, converted, skipped, and failed. Reported
  by @MKos75 and @Tobi.
