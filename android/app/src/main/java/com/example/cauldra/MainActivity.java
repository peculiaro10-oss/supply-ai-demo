package com.example.cauldra;

import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;

/**
 * Cauldra's session-restore flow depends on the refresh cookie
 * (SUPPLY_AI_REFRESH_COOKIE_SAMESITE=none; SUPPLY_AI_REFRESH_COOKIE_SECURE=true
 * on the backend) surviving a full app restart. The app's WebView runs from
 * its own local-bundle origin (https://localhost, per capacitor.config.json's
 * androidScheme) and calls a different origin
 * (https://cauldra.up.railway.app) for POST /auth/refresh with
 * credentials:"include" — from Android's CookieManager's point of view that
 * is a cross-site ("third-party") cookie exchange, and Android's
 * CookieManager withholds third-party cookies by default unless a WebView
 * explicitly opts in. Nothing in this project previously configured that
 * explicitly (the original MainActivity was an empty BridgeActivity
 * subclass) — this makes the opt-in explicit and unconditional instead of
 * depending on whatever @capacitor/android's internal default happens to be
 * for the installed version.
 *
 * Nothing else about WebView behavior is changed: no mixed-content, SSL, or
 * file-access setting is touched, and no token/secret is read, stored, or
 * logged here.
 */
public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        CookieManager cookieManager = CookieManager.getInstance();
        // Baseline (first-party) cookie acceptance. On by default in
        // practice, but made explicit here so this doesn't silently depend
        // on a platform default either.
        cookieManager.setAcceptCookie(true);

        WebView webView = getBridge().getWebView();
        if (webView != null) {
            // The actual fix: without this, Android's CookieManager can
            // withhold the cross-site refresh cookie even though the
            // backend already sends it with SameSite=None; Secure=true —
            // SameSite=None cookies are still, at the Android WebView
            // platform level, subject to the separate third-party-cookie
            // gate and need this explicit per-WebView opt-in.
            cookieManager.setAcceptThirdPartyCookies(webView, true);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        // Android's WebView cookie store persists to disk asynchronously.
        // onPause() is the last lifecycle callback Android reliably invokes
        // before a backgrounded app becomes eligible to be killed (the user
        // swiping it away, or the OS reclaiming memory) — flushing here
        // gives any cookie set during this session (in particular, the
        // refresh cookie issued at login) its best chance of having reached
        // disk before the process disappears, without needing a flush call
        // wired into every individual network request.
        CookieManager.getInstance().flush();
    }
}
