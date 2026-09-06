# Cauldra — Android & iOS Packaging (Capacitor)

## 0. What this stage is, and what it is NOT

This wraps the **existing, unmodified** Cauldra web frontend (`index.html`) in
native Android and iOS shells via Capacitor. It does **not** create a second
UI, does not touch `main.py` business logic, and does not point the native
apps at a production server yet (`server.url` is intentionally left unset —
see §5).

**Source of truth: `frontend/`. `www/` is generated, not committed.**
`frontend/` is the real web app and the only place you ever hand-edit code.
`www/` — the folder `capacitor.config.json`'s `webDir` points Capacitor at —
is a disposable, regenerated **copy** of `frontend/`, produced by
`npm run build:www` (see §4). It is **not** in this repo's committed history
and does not need to be; regenerate it any time with one command rather than
hand-maintaining it. Never edit anything under `www/` directly — it will be
silently overwritten the next time `npm run build:www` (or
`npm run cap:sync:android`, which runs it first) runs.

## 1. Current state — Android platform generated, iOS still pending a Mac

An earlier pass was done in a sandboxed environment with no network access,
so `npm install`/`npx cap add` could not run then. That has since changed:

- `npm install` — done. `@capacitor/core`, `@capacitor/cli`,
  `@capacitor/android`, `@capacitor/ios` are all installed at `8.5.1`
  (`node_modules/`, `package-lock.json`).
- `npx cap add android` — done. A real `android/` native project now exists
  (Gradle wrapper, `AndroidManifest.xml`, `build.gradle` with
  `applicationId "com.example.cauldra"` — matches `capacitor.config.json`).
- `npx cap sync android` — done, succeeded cleanly (`npx cap doctor` reports
  "Android looking great!").
- `npx cap add ios` — **not run**: this environment has no macOS/Xcode,
  which `cap add ios` requires. Run it yourself on a Mac when you're ready
  for iOS; nothing else in this doc depends on it.

Building/signing an actual APK still requires Android Studio (or a
command-line Android SDK + a JDK) on the machine that opens `android/` —
neither was available in this environment, so that step (and any real
device/emulator testing) is still yours to run. See §4.

## 2. What WAS prepared here

- `package.json` — declares the four required Capacitor packages, plus
  `build:www`/`cap:sync:android`/`cap:sync:ios` scripts that regenerate
  `www/` from `frontend/` before syncing (§4).
- `capacitor.config.json` — app name, app ID, `webDir: "www"`, secure scheme
  defaults. `server.url` deliberately omitted (§5).
- `scripts/build-www.js` — the actual regeneration logic (plain Node
  `fs.rmSync`/`fs.cpSync`, no shell-specific `rm -rf`/`robocopy` branching,
  so it runs identically on Windows/macOS/Linux). Deletes `www/` and
  recreates it as a full copy of `frontend/`. Run directly via
  `npm run build:www`, or indirectly via `npm run cap:sync:android`.
- `www/` — generated output, not hand-maintained (see the source-of-truth
  note in §0).

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

## 4. Commands (in this project's root, next to `package.json`)

Steps 1–2 for Android are **already done** (§1) — `node_modules/`,
`package-lock.json`, and `android/` all exist and are in sync. What's left is
yours to run (this environment has no Android Studio/JDK or Xcode/macOS):

```bash
# Already done for Android — re-run any time you add/remove a Capacitor
# package, or if you ever delete node_modules/:
npm install

# Already done — android/ already exists. Re-running is safe (Capacitor
# detects it and no-ops) but unnecessary unless you deliberately remove
# android/ and want to regenerate it from scratch.
npx cap add android
npx cap add ios          # requires macOS + Xcode — not run in this pass

# Whenever anything under frontend/ changes, or before any Android build:
# regenerates www/ from frontend/, THEN syncs it into android/. This is the
# one command you need for "make sure Android has the latest web code" —
# never hand-copy frontend/ into www/ yourself.
npm run cap:sync:android

# Same idea for iOS, once §1's iOS platform exists:
npm run cap:sync:ios

# If you only want to regenerate www/ without syncing any native platform:
npm run build:www

# Open in the native IDE to build/run on a device or emulator — the actual
# remaining step, requires Android Studio installed on your machine.
npx cap open android      # opens Android Studio
npx cap open ios          # opens Xcode, once §1's iOS platform exists
```

## 5. `server.url` — intentionally not set; API backend — now Railway by default

`server.url` in `capacitor.config.json` is still intentionally unset: the app
shell itself (HTML/JS/CSS) still loads from the **local bundled copy** in
`www/`, via Capacitor's local `https://localhost` scheme (see §7 on why
`https` was chosen) — that part hasn't changed and doesn't need to.

What HAS changed is separate from `server.url`: where the app's own `fetch()`
calls go once it's running. `resolveApiBaseUrl()` (top of `app.js`) now has a
real production default for native builds — no dev/localhost fallback left in
that path:

```
window.CAULDRA_API_BASE_URL  →  <meta name="cauldra-api-base-url">  →
window.SUPPLY_AI_API_URL  →  same-origin (N/A for a local bundle)  →
NATIVE_PRODUCTION_API_BASE_URL = https://cauldra.up.railway.app
```

A native build with none of the three overrides set now talks to
**`https://cauldra.up.railway.app`** — the real production backend — out of
the box. There is no `127.0.0.1`/localhost fallback in the native path any
more; if `NATIVE_PRODUCTION_API_BASE_URL` were ever emptied out by mistake,
`resolveApiBaseUrl()` throws immediately instead of silently pointing at the
device itself. See §10 for the full before/after of this change.

For local device/emulator testing against a **different** (e.g. local dev)
backend instead of production, override it with the meta tag in
`www/index.html`, exactly as before:

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
2. **OS-level camera permission**, added below. `android/` now exists (§1),
   but this permission has **not** been added to it yet — it's not required
   merely to sync/open the project, only to use the scanner on a real device.
   Add it before testing the scanner. (`ios/` still needs to be generated on
   a Mac first — §1.)

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
good candidate for that source. `android/` now exists (§1); this hasn't been
run yet — it's a quick follow-up whenever you're ready (also needs `ios/`
for the iOS half, which still requires a Mac to generate — §1).

## 10. Offline-first architecture on Android/iOS

Cauldra's offline layer (IndexedDB-backed outbox, sync engine, per-feature
offline fallbacks — see the "OFFLINE-FIRST" block near the top of the main
`<script>` in `index.html`) was built to work identically across the web
deployment and this Capacitor packaging, without relying on Chrome-only
behavior. What changed and why:

**`www/` regeneration is now a script, not a manual copy.** The web frontend
lives in `frontend/` (`index.html`, `css/`, `js/`, `assets/`, `sw.js`) — the
source of truth (§0); `www/` is Capacitor's `webDir` and is always a full,
disposable copy of it, produced by `scripts/build-www.js`
(`npm run build:www`). `npm run cap:sync:android` runs that regeneration
first and then `npx cap sync android`, so a single command always keeps
`android/`'s bundled copy current — there is no manual `rm -rf`/`robocopy`
step to remember or forget any more, and `www/` is never hand-edited or
committed (§0). `frontend/js/app.js` and `www/js/app.js` are byte-identical
after every regeneration, by construction — there is no longer any way for
them to silently drift apart the way an earlier pass's manual copy allowed.

**Backend URL resolution (`resolveApiBaseUrl()`, top of `app.js`) — the
native fallback is now the real production backend, not localhost.** The old
logic fell back to `location.origin` whenever the page loaded over
`http:`/`https:`. That's correct for the web deployment (main.py serves this
same file, same origin) but was **wrong** for a packaged app: Capacitor loads
the bundle from a fixed synthetic origin (`https://localhost`, per
`androidScheme`/`iosScheme` in `capacitor.config.json`) where nothing is
listening — so a plain `http://127.0.0.1:8000` dev-only fallback used to be
the last resort there, which never worked against a real device. The
resolution order is now:

1. `window.CAULDRA_API_BASE_URL` (set this in a tiny inline script in
   `www/index.html`, before the main script tag, if you'd rather not edit the
   meta tag per build)
2. `<meta name="cauldra-api-base-url" content="...">` (in `www/index.html`'s
   `<head>` — the simplest place to set it)
3. `window.SUPPLY_AI_API_URL` (back-compat with the prior override name)
4. Same-origin (`location.origin`) — correct for the web deployment,
   unreachable in a native build since step 5 catches that case first
5. **Native build with none of the above set:** now resolves to the
   `NATIVE_PRODUCTION_API_BASE_URL` constant (top of `app.js`), currently
   `https://cauldra.up.railway.app` — the real Railway production backend,
   not a placeholder. There is no `127.0.0.1`/localhost fallback left in the
   native path; if that constant were ever emptied out, `resolveApiBaseUrl()`
   throws immediately instead of silently reverting to a device-local
   address. Moving the backend later (e.g. to
   `https://api.cauldra.cohren.com`) is a one-line edit to that constant —
   nothing else in the function needs to change.

The backend's `SUPPLY_AI_CORS_ORIGINS` env var already needs to allow
requests from a native build's origin. Since native builds now call
`https://cauldra.up.railway.app` directly (not `location.origin`, which stays
the synthetic `https://localhost` Capacitor origin only for loading the
bundle itself), confirm whatever origin Railway's CORS config expects for
this app is already covered — no code change needed there, just verify the
env var.

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
actual skip-on-native branch still has not been exercised end-to-end on a
real device/emulator (no Android Studio/emulator available in this
environment, per §1); `android/` now exists structurally, so this is now a
device/emulator-testing gap rather than a "platform doesn't exist yet" one.

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
- The Service-Worker-skip branch and the native backend-URL resolution to
  `https://cauldra.up.railway.app` — `android/` exists and `npx cap sync
  android` succeeded (§1), but neither has been exercised on an actual
  device/emulator (no Android Studio/emulator available in this
  environment). Confirming Railway's CORS config actually accepts requests
  from the packaged app is part of that same untested step.

