# Brand, theme and library workflow review

The approved SVG brand assets are used in login/setup, navigation, app loading,
favicons and installable app icons. Wordmark fonts are embedded for offline use.

The dark/light palettes share the approved violet/lilac/teal identity. Surfaces,
input borders, hover, selection, focus, disabled controls and log severity colors
follow the active theme. Live Logs uses a light terminal surface in light mode.

Dashboard shortcut labels wrap within the full clickable/hover surface, including
at narrow widths. My Artists exposes the existing explicit discovery refresh via
`GET /api/pinned_artists?refresh=true`; the current list remains visible while
refreshing and is preserved on failure.

Metadata Rebuild now groups release metadata and artist credits with a second
stage for file/folder naming. Existing naming links open that stage. The legacy
feature-fixer route opens the release review instead of the old heuristic fixer.
Release-based recording credits remain separate from release Album Artist; this
change does not claim that provider matches are infallible. File organization
still has its own preview/apply step and is not silently bundled into tag writes.
AcoustID verification moves from System to Library and remains admin-only.

The last setup step offers a read-only library inventory. When selected, a
successful setup starts that inventory and opens the review workspace. Merely
opening an onboarding URL cannot start a scan. Declining the option opens Home;
failure to start the optional inventory does not invalidate successful setup.
Writing album metadata and moving files remain explicit reviewed actions.
The wizard's Next button cancels its native click default before switching to
the final step, preventing the newly rendered submit button from being activated
by the same click. A regression test covers this premature-submission case.

Validation includes frontend unit tests for refresh/error preservation,
onboarding opt-in/out/failure, legacy navigation and integrated naming. The local
visual demo uses synthetic API data and never forwards actions to real services.

Final checks: production build and TypeScript pass; 55 frontend tests pass across
22 files. Browser checks pass for 16 desktop/mobile views, including hover-label
bounds, light/dark log surfaces, explicit artist refresh and both legacy routes.
