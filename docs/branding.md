# VeryDisco brand assets

The approved vector artwork lives in `frontend/public/brand/`. The supplied
SVG geometry and colors are preserved. Non-rendering provenance metadata is
removed from the distributed SVGs; horizontal wordmarks embed the Sora font
so the lettering does not depend on installed fonts or network access.
Sora is distributed under the SIL Open Font License, included as `Sora-OFL.txt`.

`BrandLogo` selects the light or dark artwork using the application theme:

- Full wordmark: login, initial setup, expanded desktop and mobile navigation.
- Symbol: collapsed navigation, mobile toolbar and initial app loading state.
- App icon: SVG/PNG favicon, 180px Apple touch icon, 192px and 512px PWA icons.
- Maskable icon: separate opaque, square 512px asset. The symbol fits within
  the central safe area so the operating system can apply its own icon mask.

Monochrome and alternate dark app-icon originals are also available in the
brand directory. Functional music/navigation icons are independent of branding.

To regenerate browser/PWA PNGs after updating the approved SVGs, run
`node scripts/build_brand_assets.mjs` with `sharp` installed, or set
`SHARP_MODULE` to a file URL pointing to an existing sharp module. The checked-in
assets are ready for production and normal builds need no extra dependencies.
