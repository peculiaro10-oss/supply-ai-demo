# Cauldra native packaging

`frontend/` is the only editable frontend source. `www/` and
`android/app/src/main/assets/public/` are generated outputs.

## Android workflow

From the repository root, install locked dependencies once (and whenever the
lockfile changes), then prepare before every Android build:

```sh
npm ci
npm run android:prepare
```

Preparation runs build:www, Capacitor Android sync, then full SHA-256 parity
verification. `npm run cap:sync:android` is an alias. `npm run cap:sync` prepares
the web bundle, syncs installed platforms, and checks Android parity.
`npm run build:www` regenerates only www. A raw `npx cap sync android` does not
regenerate frontend output; Gradle still refuses stale output.

With JDK 21 configured, run `build-apk.bat` on Windows, or open Android Studio
after preparation and choose Build. Set Android Studio's Gradle JDK to a full
JDK 21 with jlink. Every normal variant's preBuild depends on
verifyCauldraFrontend, an always-executed task. It exits nonzero on stale/missing
assets, manifest/config drift, or missing App/InAppBrowser plugins. It never
calls npm or Gradle recursively. Do not exclude this task with Gradle -x.

Do not manually copy app.js or index.html between generated directories. If
verification fails, edit frontend/, fix the missing dependency/source issue,
then rerun android:prepare. `tests/test_native_bundle.cjs` demonstrates negative
checks using its own disposable copy after preparation.

`www/build-manifest.json` contains the app version, content-derived source
version/build ID, configuration and lockfile hashes, and hashes of every source
asset, including sw.js. Capacitor copies the manifest into Android. All copied
assets must remain byte-identical across the three trees.

## API, cookies and returns

The packaged page loads locally; keep server.url unset. Android's configured
origin is https://localhost. The native API default remains
https://cauldra.up.railway.app. The API meta override is empty in source; any
intentional future override belongs in frontend/index.html before preparation.

Credentialed CORS must explicitly allow https://localhost. The backend scopes
Secure/HttpOnly/SameSite=None refresh cookies to the allowed native origin;
ordinary web cookie attributes remain configured as before. Existing Android
CookieManager settings accept third-party cookies and flush on pause. A real
device must still verify refresh after backgrounding and process restart.

Fresh email callbacks use the HTTPS backend page /auth/email-verified, then the
persisted platform route. AndroidManifest registers only
cauldra://auth/email-verified for onboarding; the existing payment-return scheme
is retained. See NATIVE_EMAIL_VERIFICATION.md and INSTALLATION_GUIDE.md delivered
with this update for provider setup and runtime checks.

## iOS

No iOS project exists in this source. The iOS synthetic scheme is corrected to
capacitor (WKWebView cannot register http/https as custom schemes). On macOS,
generate the iOS project with the locked dependencies, register the cauldra URL
scheme in Info.plist, and run npm run cap:sync:ios. The shared App listener
already handles native_ios challenges. Add capacitor://localhost to production
CORS only when deploying that target. iOS URL dispatch and cookie persistence
require tests on iOS; neither is claimed here.

The application ID remains com.example.cauldra; signing and store identity are
outside this focused pass. No camera, scanner, or offline Phase 2 changes are
included.
