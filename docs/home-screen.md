# Home Screen app

Clonepage includes a web app manifest and Apple Home Screen metadata. Add the HTTPS homepage to your iPhone Home Screen from Safari using **Share → Add to Home Screen**. Leave **Open as Web App** enabled if Safari offers that option. The shortcut is named Clonepage, uses the branded icon, and opens in standalone mode.

After first deploying these settings, remove the previous Home Screen shortcut and add it again so iOS picks up the new launch settings and icon. Tailscale must still be connected to reach this private installation.

The manifest starts at `/` and keeps all Clonepage routes within its scope. Internal Home, Infrastructure, and saved-copy navigation stays inside the app; external service/article links retain their existing behavior.

The page respects notch and home-indicator safe areas in portrait and landscape. Browser theme colors track Clonepage's light/dark toggle. Touch-device forms use readable 16px text while preserving pinch-to-zoom. Desktop spacing and breakpoints are retained.

This adds app presentation only. It does not add a service worker, offline caching, or background sync. Saved reading copies remain on the NAS and require a network connection to open.

Icon assets are in `public/icons/`, with the vector source in `app.svg`. The 180px Apple touch icon is `public/apple-touch-icon.png`; the manifest uses opaque 192px and 512px PNG icons with the central mark inside the maskable safe area.
