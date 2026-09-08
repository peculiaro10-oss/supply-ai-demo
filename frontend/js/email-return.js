/* No Supabase session tokens or account details are persisted or displayed. */
(async () => {
    'use strict';
    const params = new URLSearchParams(location.search), challenge = params.get('challenge');
    const code = params.get('code') || '';
    history.replaceState({}, document.title, location.pathname);
    const status = document.getElementById('email-return-status'), open = document.getElementById('email-return-open');
    if (params.get('purpose') !== 'onboarding' || !/^[a-f0-9]{64}$/.test(challenge || '')) {
        status.textContent = 'This link is not valid. Return to Cauldra and request a new verification email.'; return;
    }
    try {
        const response = await fetch('/onboarding/email/verify/confirm', {method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({challenge_id:challenge, code})});
        const data = await response.json();
        if (!response.ok || data.status !== 'verified') {
            status.textContent = typeof data.detail === 'string' ? data.detail : 'Verification is not complete. Return to Cauldra and request a new email.'; return;
        }
        const target = new URL(data.return_target);
        if (data.platform === 'web' && target.protocol === 'https:') {
            target.searchParams.set('cauldra_email_verify','1'); target.searchParams.set('ev_purpose','onboarding');
            target.searchParams.set('challenge',challenge);
            status.textContent = 'Your email has been verified. Returning to Cauldra…';
            open.href = target.href; open.hidden = false; location.replace(target.href);
        } else if (['native_android','native_ios'].includes(data.platform) && target.href === 'cauldra://auth/email-verified') {
            target.searchParams.set('purpose','onboarding'); target.searchParams.set('challenge',challenge);
            status.textContent = 'Your email has been verified. Return to Cauldra to continue.';
            document.getElementById('email-return-help').textContent = 'If Cauldra does not open, install or open the app and choose “I’ve verified — check now”.';
            open.textContent = 'Open Cauldra'; open.href = target.href; open.hidden = false;
            // User gesture works in email-client browsers that block auto-launch.
            location.href = target.href;
        } else throw new Error('invalid_return');
    } catch (_) { status.textContent = 'Verification could not be completed. Return to Cauldra to check your status or request a new email.'; }
})();
