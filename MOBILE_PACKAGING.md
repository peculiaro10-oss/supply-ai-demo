# Cauldra native packaging

`frontend/` is the only editable frontend source. `www/` and
`android/app/src/main/assets/public/` are generated outputs.

## Android workflow

From the repository root, install locked dependencies once (and whenever the
lockfile changes), then prepare before every Android build **with an explicit
target**:

```sh
npm ci
npm run android:prepare:prod   # production -> https://cauldra.cohren.com
npm run android:prepare:qa     # QA         -> https://cauldra-qa.up.railway.app
```

Preparation runs build:www for that target, Capacitor Android sync, then full
SHA-256 parity verification. There is no default target: `npm run build:www`,
`npm run android:prepare`, `npm run cap:sync` and `npm run cap:sync:android` fail
unless `CAULDRA_BUILD_TARGET` is set. A raw `npx cap sync android` does not
regenerate frontend output; Gradle still refuses stale output.

With JDK 21 configured, run `build-apk.bat production` (or `build-apk.bat qa`) on
Windows, or open Android Studio
after preparation and choose Build. Set Android Studio's Gradle JDK to a full
JDK 21 with jlink. Every normal variant's preBuild depends on
verifyCauldraFrontend, an always-executed task. It exits nonzero on stale/missing
assets, manifest/config drift, or missing App/InAppBrowser plugins. It never
calls npm or Gradle recursively. Do not exclude this task with Gradle -x.

Do not manually copy app.js or index.html between generated directories. If
verification fails, edit frontend/, fix the missing dependency/source issue,
then rerun android:prepare:<qa|prod>. `tests/test_native_bundle.cjs` demonstrates negative
checks using its own disposable copy after preparation.

`www/build-manifest.json` contains the app version, content-derived source
version/build ID, configuration and lockfile hashes, and hashes of every source
asset, including sw.js. Capacitor copies the manifest into Android. All copied
assets must remain byte-identical across the three trees.

## API, cookies and returns

The packaged page loads locally; keep server.url unset. Android's configured
origin is https://localhost.

Every build declares its backend explicitly (BUILD-001). Targets live only in
scripts/build-targets.json: `qa` -> https://cauldra-qa.up.railway.app and
`production` -> **https://cauldra.cohren.com**, the canonical production address.
https://cauldra.up.railway.app is the underlying Railway service URL for that
same deployment; it is infrastructure, never a build target. Build with
`build-apk.bat <qa|production>` or `npm run android:prepare:<qa|prod>`; there is
no default target. Confirm every APK before installing or distributing it with
`npm run verify:apk -- --apk=<path> --target=<name>`. The fallback constant
NATIVE_PRODUCTION_API_BASE_URL in frontend/js/app.js must equal the production
target; the parity gate fails otherwise. Never hand-inject an override and never
exclude verifyCauldraFrontend with Gradle -x.

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
