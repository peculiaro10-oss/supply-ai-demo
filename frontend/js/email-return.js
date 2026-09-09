/* No Supabase session tokens or account details are persisted or displayed. */
(async () => {
    'use strict';
    const params = new URLSearchParams(location.search), challenge = params.get('challenge');
    const code = params.get('code') || '';
    const status = document.getElementById('email-return-status'), open = document.getElementById('email-return-open');
    const clearQuery = () => history.replaceState({}, document.title, location.pathname);
    if (params.get('purpose') !== 'onboarding' || !/^[a-f0-9]{64}$/.test(challenge || '')) {
        clearQuery();
        status.textContent = 'This link is not valid. Return to Cauldra and request a new verification email.'; return;
    }
    try {
        const response = await fetch('/onboarding/email/verify/confirm', {method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({challenge_id:challenge, code})});
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'verified') {
            // Preserve the one-time PKCE code across reload for transient server
            // and rate-limit failures. Definitive client errors are safe to clear.
            if (response.status < 500 && response.status !== 429) clearQuery();
            status.textContent = typeof data.detail === 'string' ? data.detail : 'Verification is not complete. Return to Cauldra and request a new email.'; return;
        }
        const target = new URL(data.return_target);
        if (data.platform === 'web') {
            const expected = new URL(data.expected_return_origin);
            if (target.protocol !== 'https:' || expected.protocol !== 'https:' || target.origin !== expected.origin
                    || target.username || target.password || expected.username || expected.password
                    || expected.pathname !== '/' || expected.search || expected.hash) throw new Error('invalid_return');
            target.searchParams.set('cauldra_email_verify','1'); target.searchParams.set('ev_purpose','onboarding');
            target.searchParams.set('challenge',challenge);
            status.textContent = 'Your email has been verified. Returning to Cauldra…';
            open.textContent = 'Return to Cauldra'; open.href = target.href; open.hidden = false;
            clearQuery();
            // Yield once so the real fallback anchor is usable even when an
            // embedded email browser suppresses the automatic navigation.
            setTimeout(() => location.replace(target.href), 0);
        } else if (['native_android','native_ios'].includes(data.platform) && target.href === 'cauldra://auth/email-verified') {
            target.searchParams.set('purpose','onboarding'); target.searchParams.set('challenge',challenge);
            status.textContent = 'Your email has been verified. Return to Cauldra to continue.';
            document.getElementById('email-return-help').textContent = 'If Cauldra does not open, install or open the app and choose “I’ve verified — check now”.';
            open.textContent = 'Open Cauldra'; open.href = target.href; open.hidden = false;
            clearQuery();
            // User gesture works in email-client browsers that block auto-launch.
            setTimeout(() => location.href = target.href, 0);
        } else throw new Error('invalid_return');
    } catch (_) {
        // A transport failure deliberately leaves the captured query available
        // for reload; no token/session is persisted anywhere.
        status.textContent = 'Verification could not be completed. Reload this page or return to Cauldra to check your status.';
    }
})();
