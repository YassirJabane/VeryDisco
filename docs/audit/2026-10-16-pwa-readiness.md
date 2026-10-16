# Android and iOS PWA readiness

Follow-up to the brand/theme and library workflow changes.

## Changes

- Valid scoped standalone manifest with 192/512px icons and a separate opaque
  maskable icon; the symbol stays inside the central 40% safe radius.
- Dedicated 180px Apple touch icon, iOS app title and mobile-capable metadata.
- Enable viewport-fit=cover and browser zoom. Use dynamic viewport height and
  safe-area insets for the toolbar, drawer, login/setup, content and audio player.
- Browser color scheme/theme color follows the saved theme before first paint;
  the iOS status-bar hint is updated for light/dark, subject to platform behavior.
- Precache only static assets. Offline SPA routes fall back to the app shell;
  API, health and asset navigation exclusions remain explicit. No API/audio
  runtime caching is configured. Offline controls explain the server requirement.
- Update prompt instead of activating/reloading over unsaved work. Explicit
  activation handles updates on the very first installed page as well.
- Revalidate service worker, manifest and HTML entrypoints after deployment.
- Document HTTPS and Android/iOS installation steps in the README.

## Verification and limits

Production build/typecheck and 59 frontend tests pass. All 368 backend/audit
tests pass, including worker/manifest/HTML cache-header regression cases.
Real service-worker browser checks verify cached offline deep links, unavailable
offline API requests, absence of API/audio in the cache, waiting updates keeping
in-memory state, and explicit activation/reload. Manifest parsing reports no
errors; Chromium's installability check in a persistent non-incognito profile
also returns no errors. Mobile viewport checks cover Pixel 7/dark and iPhone 13/light in Chromium
emulation without horizontal overflow. Icons and maskable alpha/safe radius are
checked from the PNG pixels.

These checks do not constitute installation tests on physical Android/iOS or
Safari/WebKit. The local Docker CLI is unavailable; the repository's existing CI
container smoke job remains the production-container gate on beta publication.
For a LAN deployment, HTTPS must be configured externally (localhost exemptions
are for local development, not a phone visiting a server's HTTP LAN address).

## Primary references

- [MDN installation requirements](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)
- [WebKit viewport/safe areas](https://webkit.org/blog/7929/designing-websites-for-iphone-x/)
- [Apple status-bar metadata](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariHTMLRef/Articles/MetaTags.html)
- [Vite PWA React registration/update lifecycle](https://vite-pwa-org.netlify.app/frameworks/react.html)
