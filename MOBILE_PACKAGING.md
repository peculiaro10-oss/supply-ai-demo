# Cauldra — Android & iOS Packaging (Capacitor)

## 0. What this stage is, and what it is NOT

This wraps the **existing, unmodified** Cauldra web frontend (`index.html`) in
native Android and iOS shells via Capacitor. It does **not** create a second
UI, does not touch `main.py` business logic, and does not point the native
apps at a production server yet (`server.url` is intentionally left unset —
see §5).

## 1. IMPORTANT — why this couldn't be fully finished automatically

This preparation was done in a sandboxed environment **with no network
access** (confirmed: `registry.npmjs.org` returns `403 host_not_allowed`
here). That means the following could **not** be executed on your behalf,
because they all require downloading packages from npm:

- `npm install` (installing `@capacitor/core`, `@capacitor/cli`,
  `@capacitor/android`, `@capacitor/ios`)
- `npx cap add android` (generates the actual `android/` native project)
- `npx cap add ios` (generates the actual `ios/` native project)

I did **not** fabricate fake `android/`/`ios/` folders to make it look
finished — that would be dishonest and would waste your time when it didn't
actually build. Instead, everything below is prepared so that running the
commands yourself (on a machine with internet access and, for iOS, a Mac) is
a single, short pass.

## 2. What WAS prepared here

- `package.json` — declares the four required Capacitor packages.
- `capacitor.config.json` — app name, app ID, `webDir`, secure scheme
  defaults. `server.url` deliberately omitted (§5).
- `www/index.html` — a **copy** of your existing `index.html`, placed where
  Capacitor's build step expects a static web root. This is the same file,
  not a rewrite or a second frontend.

## 3. Static assets — RESOLVED

Your real `/assets` folder (icons, `manifest.json`, vendored Tailwind CSS,
`html5-qrcode`, FontAwesome CSS + webfonts) has been copied into
`www/assets/`, in the exact folder structure `index.html` actually
references — I checked every single `/assets/...` path in the file and
cross-verified each one resolves, including the FontAwesome CSS's own
internal `../webfonts/...` references. This blocking gap from the previous
pass is closed; `www/` is now a complete, working copy of the frontend.

Two things intentionally were *not* included, and don't need to be:
- `.ttf` fallback fonts referenced by FontAwesome's CSS as a legacy format
  for very old browsers — irrelevant here since Capacitor's native WebViews
  (modern Chromium/WebKit) fully support the `.woff2` files that were
  provided.
- `fa-v4compatibility` font (old FontAwesome-4 icon-name aliases) — I
  checked every icon class used in `index.html` and none of them rely on
  it (e.g. `fa-gear` is a normal current-generation icon, not a v4 alias).

## 4. Exact commands to run yourself (in this project's root, next to `package.json`)

```bash
# 1. Install Capacitor (resolves to whatever is actually latest right now —
#    I intentionally did not hardcode a version number I couldn't verify)
npm install

# 2. Generate the native projects
npx cap add android
npx cap add ios          # requires macOS + Xcode

# 3. Copy www/ into each native project (repeat any time www/ changes)
npx cap sync

# 4. Open in the native IDEs to build/run on a device or simulator
npx cap open android      # opens Android Studio
npx cap open ios          # opens Xcode
```

## 5. `server.url` — intentionally not set

Per your instructions, this stage does not point the packaged app at a
production domain. Right now the app will load the **local bundled copy** of
`index.html` from `www/` (via Capacitor's local `https://localhost` scheme —
see §7 on why `https` was chosen). Your existing API-base resolution in
`index.html` already accounts for this:

```
window.CAULDRA_API_BASE_URL  →  <meta name="cauldra-api-base-url">  →
window.SUPPLY_AI_API_URL  →  same-origin (N/A for a local bundle)  →
http://127.0.0.1:8000 (dev-only fallback)
```

For local device/simulator testing against your dev backend, the simplest
option **for now** is setting the meta tag in `www/index.html`:

```html
<meta name="cauldra-api-base-url" content="http://10.0.2.2:8000">
```

`10.0.2.2` is the Android emulator's alias for your host machine's
`localhost`; a real device on the same Wi-Fi needs your machine's LAN IP
instead. iOS Simulator can use `127.0.0.1` directly.

**Android cleartext note:** Android blocks plain `http://` by default (API
28+). For local emulator testing only, you'll need a network security config
permitting cleartext to that one dev host — do **not** carry this into a
production build. I have not added this, since it's a local-dev-only
concern and out of scope for this stage; happy to add it in the next pass if
you want emulator testing now.

When you're ready for a real production configuration (later stage, per your
instructions), `server.url` gets set to your real HTTPS domain, and that
domain's origin needs adding to `ALLOWED_ORIGINS` in `main.py`'s CORS config
(already present and configurable — no code change needed, just an env var).

## 6. App identity

- **App name:** `Cauldra`
- **App ID:** `com.example.cauldra` — **temporary placeholder.** I searched
  the entire codebase for any already-established production domain and
  found none (only the documentation placeholder `https://api.example.com`
  and third-party service URLs like Paystack/Termii/Resend). Per your
  instructions, I did not invent a fake company domain. `example.com` is the
  IETF-reserved placeholder domain used for exactly this situation — it is
  **not** a real identity and **must** be changed to your real reverse-domain
  ID (e.g. `com.cauldra.app`, once you control that domain) before any store
  submission. Both app stores will reject `com.example.*` at submission
  time regardless, so this can't accidentally slip through.

## 7. Camera / barcode scanning

Your scanner (`Html5Qrcode`, vendored locally at
`/assets/vendor/html5-qrcode-2.3.8.min.js`) and your invoice-snapshot camera
capture both use plain browser `getUserMedia()` — not a Capacitor camera
plugin. This should keep working unchanged inside the native WebViews, with
two conditions:

1. **Secure context.** `getUserMedia()` only works in a secure context.
   `capacitor.config.json` already sets `androidScheme`/`iosScheme` to
   `https` (Capacitor's own default, made explicit here) precisely so the
   locally-bundled page is served as `https://localhost` instead of
   `file://`, which satisfies this requirement.
2. **OS-level camera permission**, added below. These files don't exist yet
   (they're generated by `npx cap add android`/`ios` in §4) — add these
   *after* running those commands.

**Android** — add to `android/app/src/main/AndroidManifest.xml`:
```xml
<uses-permission android:name="android.permission.CAMERA" />
<uses-feature android:name="android.hardware.camera" android:required="false" />
```

**iOS** — add to `ios/App/App/Info.plist`:
```xml
<key>NSCameraUsageDescription</key>
<string>Cauldra uses your camera to scan product barcodes and QR codes for fast inventory lookup and checkout.</string>
```

No other permissions were added — no contacts, location, microphone, SMS,
phone, or storage, matching your instruction to request only what's
actually used.

**Known open question (device-testing required, not assumed):** Capacitor's
default Android bridge generally auto-grants in-WebView `getUserMedia`
requests once the app holds the OS-level `CAMERA` runtime permission — but
nothing in this project currently *triggers* that Android runtime-permission
dialog (no `@capacitor/camera` plugin, no custom `MainActivity` code). If
testing on a real Android device shows the scanner can't get a camera stream,
the minimal fix is a few lines in `MainActivity` requesting `CAMERA` at
launch — not a new scanner, not a new plugin, just prompting for a
permission the manifest already declares. I have not added this speculatively
since I can't verify on-device behavior without a network connection or
hardware — flagging it here per your instruction to report suspected native
incompatibilities rather than silently patch around them.

## 8. Authentication & Paystack

No changes were made or needed. Both already run over ordinary `fetch()`
calls to `${API_URL}/...` with the existing JWT/refresh-cookie flow and the
existing Paystack redirect-based checkout — none of that is native-plugin
dependent. The one item worth testing on a real device once `server.url`
is eventually configured (later stage, not this one): confirming Paystack's
redirect-back URL correctly re-opens the app rather than a plain browser tab.
That's a `server.url`-dependent concern, out of scope here, and explicitly
flagged rather than solved speculatively per your instructions.

## 9. App icon / branding

Real assets now in place at `www/assets/` — `icon-192.png`, `icon-512.png`,
`apple-touch-icon.png` (180×180), `favicon-32.png`, `favicon-16.png`, and
`cauldra-logo.png`, exactly as referenced by `index.html`/`manifest.json`.
Nothing was redesigned or fabricated.

For native app-icon generation specifically (the various Android
launcher-icon densities and iOS `AppIcon.appiconset` sizes Capacitor's native
projects expect, which are more sizes than a web favicon set), the standard
next step is the separate `@capacitor/assets` package, run against a single
high-resolution square source image — `cauldra-logo.png` (1254×1254) is a
good candidate for that source. That step needs network access to install
the package and hasn't been run here; it's a quick follow-up once `android/`
and `ios/` exist (§4).

## 10. Offline-first architecture on Android/iOS

Cauldra's offline layer (IndexedDB-backed outbox, sync engine, per-feature
offline fallbacks — see the "OFFLINE-FIRST" block near the top of the main
`<script>` in `index.html`) was built to work identically across the web
deployment and this Capacitor packaging, without relying on Chrome-only
behavior. What changed and why:

**`www/` was stale/missing — regenerated.** It didn't exist when this pass
started (only `capacitor.config.json`/`package.json` did). It's now a fresh
copy of the current frontend bundle, matching §3's approach. This is
a **copy**, not a build step. The web frontend now lives in `frontend/`
(`index.html`, `css/`, `js/`, `assets/`, `sw.js`), so re-run the copy of the
whole folder — `rm -rf www && cp -r frontend www` (or `robocopy frontend www /MIR`
on Windows) — any time anything under `frontend/` changes, before `npx cap sync`.

**Backend URL resolution (`resolveApiBaseUrl()`, top of the main script) —
fixed a real bug this stage exposed.** The old logic fell back to
`location.origin` whenever the page loaded over `http:`/`https:`. That's
correct for the web deployment (main.py serves this same file, same origin)
but **wrong** for a packaged app: Capacitor loads the bundle from a fixed
synthetic origin (`https://localhost`, per `androidScheme`/`iosScheme` in
`capacitor.config.json`) where nothing is listening. The resolution order is
now:

1. `window.CAULDRA_API_BASE_URL` (set this in a tiny inline script in
   `www/index.html`, before the main script tag, if you'd rather not edit the
   meta tag per build)
2. `<meta name="cauldra-api-base-url" content="...">` (in `www/index.html`'s
   `<head>` — the simplest place to set it)
3. `window.SUPPLY_AI_API_URL` (back-compat with the prior override name)
4. Same-origin (`location.origin`) — correct for the web deployment,
   unreachable in a native build since step 5 catches that case first
5. **Native build with none of the above set:** falls back to
   `http://127.0.0.1:8000` for local development *and logs a `console.warn`*
   — it does not fail silently, and it will not work against a real device or
   TestFlight/Play build. Set #1 or #2 to your real backend URL before
   building for a device.

Before shipping a native build, also add that same origin
(`https://localhost` if `server.url` stays unset per §5, or your real domain
once §5's later stage happens) to the backend's `SUPPLY_AI_CORS_ORIGINS` env
var — CORS is already fully configurable there, no code change needed.

**Service Worker — intentionally not registered inside the native shell.**
`index.html` now checks `window.Capacitor.isNativePlatform()` (the real
bridge object Capacitor injects at runtime) and skips `sw.js` registration
entirely when true. Two reasons: the Service Worker's only job is making the
app *shell* loadable offline, which is moot when that shell already ships
as local files inside the native bundle; and iOS's WKWebView (what Capacitor
uses on iOS) has an inconsistent Service-Worker support history across OS
versions, so not depending on it there removes a real source of platform risk
rather than hoping it behaves like desktop Safari/Chrome. This check has only
been verified in a plain browser (`window.Capacitor` correctly `undefined`
there, so the Service Worker still registers for the web deployment) — the
actual skip-on-native branch could not be exercised end-to-end because, per
§1, `android/`/`ios/` haven't been generated in this environment.

**The data layer itself needed no changes to be cross-engine.** IndexedDB
(outbox, `products_cache`, `suppliers_cache`), `crypto.randomUUID()` (with a
manual fallback already in place for when it's unavailable), `AbortController`
timeouts, and the `online`/`offline` events are all standard, non-vendor-
prefixed Web APIs — nothing in that layer ever depended on a Chrome-specific
API. `navigator.onLine` is also never trusted alone (every sync attempt
confirms with a real `/health` request first — see `isBackendReachable()`),
which matters here too since `navigator.onLine`'s accuracy has historically
varied more across WebView engines than in a desktop browser.

**Added:** a best-effort `navigator.storage.persist()` call at startup, so
the browser/OS is less likely to evict IndexedDB data under storage
pressure — relevant on both mobile Safari/WKWebView and Chrome; a harmless
no-op anywhere it's unsupported.

**What this means concretely for offline behavior on a real device:**
Products/Sales/Expenses created offline are written to IndexedDB and queued
in the outbox exactly as on the web — that mechanism doesn't know or care
whether it's running in a Chrome tab or a WKWebView. What's genuinely
untestable without a real Android/iOS build (flagged per this project's
"don't claim it unless it's tested" rule, not silently assumed):

- Whether IndexedDB in an actual on-device WKWebView behaves under real
  storage-pressure/eviction conditions the way it does in desktop testing.
- Whether `navigator.onLine`/the `online`/`offline` events fire reliably
  across real cellular ↔ Wi-Fi handoffs on-device (the `/health`-probe
  pattern above means a wrong `navigator.onLine` reading only delays a sync
  attempt, it can't cause a false "success" — but the actual on-device
  timing hasn't been observed).
- The Service-Worker-skip branch and the native backend-URL configuration,
  both blocked on `android/`/`ios/` not existing yet (§1).

