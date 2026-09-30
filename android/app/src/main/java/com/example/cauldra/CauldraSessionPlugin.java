package com.example.cauldra;

import android.webkit.CookieManager;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * NATIVE-SESSION-001: every successful sign-in / refresh rotates the HttpOnly
 * refresh cookie and revokes its predecessor on the server. The WebView keeps
 * the new cookie in memory and writes it to disk only in ~30 s batches, so a
 * process death inside that window (force-stop, crash, or a background kill
 * after a refresh that finished once onPause() had already flushed) left the
 * revoked predecessor on disk and the next launch signed the user out.
 *
 * The frontend calls flushCookies() right after every auth response that sets,
 * rotates or clears that cookie. It only persists what the cookie store
 * already holds: no cookie or token is read, written, returned or logged here.
 */
@CapacitorPlugin(name = "CauldraSession")
public class CauldraSessionPlugin extends Plugin {
    @PluginMethod
    public void flushCookies(PluginCall call) {
        // Plugin methods run on the plugin handler thread, never the UI thread,
        // so this blocking disk write does not stall the WebView.
        CookieManager.getInstance().flush();
        call.resolve();
    }
}
