### Changed

- **The new interface now opens by default in a fresh browser.** Choosing “Back to the classic view” keeps Classic selected across tabs and restarts until “Back to New UI” is used, while command-line, OPDS, and Kobo clients keep their existing non-redirect behavior. LDAP and reverse-proxy-header installations retain the Classic login page until those authentication methods are supported by the new login API.
- **Catalog visibility choices now follow your account.** Discover visibility, hidden-book visibility, and the per-card Read/edit row carry across browsers and devices for signed-in users, while guest browsing keeps the existing browser-local settings.
