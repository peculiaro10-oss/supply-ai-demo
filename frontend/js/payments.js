/* Cauldra owns orchestration only. Paystack owns every sensitive payment field.
 * Official InlineJS v2 resumeTransaction(accessCode, callbacks) on web;
 * official Capacitor InAppBrowser hosted checkout on native. No frame access,
 * input listeners, payment field inspection, or custom card form belongs here.
 */
window.CauldraPayments = (() => {
    'use strict';
    const endpoints = {
        checkout: ['/subscription/checkout', '/subscription/checkout/confirm'],
        upgrade: ['/subscription/upgrade-checkout', '/subscription/checkout/confirm'],
        trial: ['/subscription/trial/init', '/subscription/trial/confirm'],
        onboarding: ['/onboarding/payment/init', '/onboarding/payment/confirm'],
        method: ['/subscription/payment-method/init', '/subscription/payment-method/confirm']
    };
    const storageKey = 'cauldra_payment_attempt_v1';
    const PHASE = Object.freeze({
        INITIALIZING: 'initializing', CHECKOUT_READY: 'checkout_ready', CHECKOUT_OPEN: 'checkout_open',
        RETURNED: 'returned', VERIFYING: 'verifying', VERIFIED: 'verified', CANCELLED: 'cancelled',
        PENDING: 'pending', FAILED: 'error'
    });
    let active = null, busy = false, verifying = false, providerOpen = false;
    let popup = null, sdkPromise = null, listeners = [], returnFocus = null;
    let savedOverflow = '', refreshTimer = null, checking = null;
    const el = id => document.getElementById(id);
    const native = () => !!window.Capacitor?.isNativePlatform?.();
    const owner = () => typeof authToken === 'string' && authToken ? `${currentUserProfile?.business_id || ''}:${currentUserProfile?.id || ''}` : 'onboarding';

    function trace(event, fields = {}) {
        const safe = {};
        for (const [key, value] of Object.entries(fields || {})) {
            if (['kind','phase','status','native','hasReference','checkoutOpened','httpStatus'].includes(key)) safe[key] = value;
        }
        console.info(`[CAULDRA_PAYMENT] ${event}`, safe);
    }
    function remember() {
        if (!active) return;
        // Keep only routing/retry state. Never persist provider authorization URL,
        // access code, card data, authorization codes, or provider secrets.
        const {kind, reference, key, values, ownerId, phase, checkoutOpened, canRestart} = active;
        sessionStorage.setItem(storageKey, JSON.stringify({kind, reference, key, values, ownerId, phase, checkoutOpened:!!checkoutOpened, canRestart:!!canRestart}));
    }
    function forget() {
        sessionStorage.removeItem(storageKey);
    }
    function restore() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
            if (saved && endpoints[saved.kind] && saved.ownerId === owner()) return saved;
        } catch (_) {}
        return null;
    }
    function status(phase, message) {
        if (active) active.phase = phase;
        el('payment-overlay').dataset.state = phase;
        el('payment-status').textContent = message;
        const retryable = [PHASE.PENDING, PHASE.CANCELLED, PHASE.FAILED].includes(phase);
        el('payment-check').hidden = !retryable || !active?.reference;
        el('payment-resume').hidden = !retryable || !active?.access_code;
        el('payment-retry-init').hidden = !active?.canRestart;
        el('payment-spinner').hidden = ![PHASE.INITIALIZING, PHASE.VERIFYING, 'opening'].includes(phase);
        el('payment-close').disabled = providerOpen;
        remember();
    }
    function show() {
        returnFocus = document.activeElement;
        savedOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        el('payment-overlay').hidden = false;
        el('payment-close').focus();
    }
    function close() {
        if (providerOpen) return; // use Paystack/native own close control
        el('payment-overlay').hidden = true;
        document.body.style.overflow = savedOverflow;
        returnFocus?.focus?.();
        if (active?.kind !== 'onboarding' && typeof authToken === 'string' && authToken) openBillingModal();
    }
    function headers(kind, key) {
        const result = {'Content-Type': 'application/json'};
        if (kind !== 'onboarding') result.Authorization = `Bearer ${authToken}`;
        if (key) result['Idempotency-Key'] = key;
        return result;
    }
    async function loadSdk() {
        if (typeof window.PaystackPop === 'function') return;
        if (!sdkPromise) sdkPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://js.paystack.co/v2/inline.js'; script.async = true;
            const timer = setTimeout(() => {script.remove(); sdkPromise = null; reject(new Error('checkout_unavailable'));}, 20000);
            script.onload = () => {clearTimeout(timer); typeof window.PaystackPop === 'function' ? resolve() : reject(new Error('checkout_unavailable'));};
            script.onerror = () => {clearTimeout(timer); script.remove(); sdkPromise = null; reject(new Error('checkout_unavailable'));};
            document.head.appendChild(script);
        });
        await sdkPromise;
    }
    function newAttempt(kind, values) {
        return {kind, values, key: crypto.randomUUID(), ownerId: owner(), phase:PHASE.INITIALIZING, checkoutOpened:false, canRestart:false};
    }
    function validateInitialization(data) {
        if (!data || typeof data !== 'object' || !data.reference || !data.access_code || !data.authorization_url) throw new Error('invalid_initialization_payload');
        const provider = new URL(data.authorization_url);
        if (provider.protocol !== 'https:' || provider.hostname !== 'checkout.paystack.com' || provider.username || provider.password) throw new Error('invalid_checkout');
    }
    async function initializeCurrent() {
        trace('PAYSTACK_INIT_REQUEST_STARTED', {kind:active.kind, phase:PHASE.INITIALIZING, native:native(), hasReference:!!active.reference});
        status(PHASE.INITIALIZING, 'Preparing your secure payment…');
        const response = await fetch(`${API_URL}${endpoints[active.kind][0]}`, {
            method:'POST', credentials:'include', headers:headers(active.kind, active.key), body:JSON.stringify(active.values)
        });
        let data = {};
        try { data = await response.json(); } catch (_) {}
        if (!response.ok) {
            if (data.detail?.reference) active.reference = data.detail.reference;
            trace('PAYSTACK_INIT_FAILED', {kind:active.kind, phase:PHASE.FAILED, httpStatus:response.status, hasReference:!!active.reference});
            throw Object.assign(new Error('initialization_failed'), {response, data});
        }
        validateInitialization(data);
        Object.assign(active, data, {phase:PHASE.CHECKOUT_READY, canRestart:false});
        remember();
        trace('PAYSTACK_INIT_SUCCESS', {kind:active.kind, phase:PHASE.CHECKOUT_READY, native:native(), hasReference:true});
        el('payment-plan').textContent = `${data.plan || 'Payment method'} · ${data.billing_interval === 'annual' ? 'Annual' : 'Monthly'}`;
        el('payment-amount').textContent = new Intl.NumberFormat('en-NG', {style:'currency',currency:data.currency || 'NGN'}).format(data.amount_kobo / 100);
        el('payment-explanation').textContent = ['method','trial','onboarding'].includes(active.kind)
            ? 'This small card verification charge will be submitted for refund after verification. Paystack handles your payment details.'
            : 'Paystack handles your payment details. Your plan updates after Cauldra confirms the payment.';
        await launch();
    }
    async function start(kind, values = {}) {
        if (busy || providerOpen || verifying || !endpoints[kind]) return;
        const saved = active || restore();
        if (saved && saved.ownerId === owner()) {
            active = saved; show();
            // A pre-initialization record is not a payment and must never be called
            // pending/unresolved. Let the user safely retry initialization.
            if (!saved.reference) {
                active.canRestart = true;
                status(PHASE.FAILED, "Paystack couldn't be opened right now. Please try again.");
                return;
            }
            if (!saved.checkoutOpened) {
                status(PHASE.VERIFYING, 'Checking the previous checkout setup…');
                await verify(false, false, true);
                return;
            }
            status(PHASE.PENDING, 'A previous Paystack checkout needs to be checked before another payment can start.');
            await verify(false);
            return;
        }
        busy = true;
        active = newAttempt(kind, values);
        remember(); show();
        el('payment-plan').textContent = kind === 'method' ? 'Update payment method' : 'Secure checkout';
        el('payment-amount').textContent = 'Calculating securely…';
        try {
            await initializeCurrent();
        } catch (error) {
            // A server-owned reference may represent an initialization whose
            // transport outcome is uncertain. Do not manufacture another charge.
            // If no reference exists, no server-owned payment attempt exists and
            // a fresh initialization is safe.
            active.canRestart = !active?.reference;
            status(PHASE.FAILED, "Paystack couldn't be opened right now. Please try again.");
            el('payment-resume').hidden = !active?.access_code;
        } finally { busy = false; }
    }
    async function retryInitialization() {
        if (busy || providerOpen || verifying || !active || !active.canRestart) return;
        const {kind, values} = active;
        // Fresh idempotency key is intentional here. canRestart is only granted
        // when the client never received/opened a Paystack checkout, or after the
        // backend has definitively classified the previous attempt cancelled/failed.
        active = newAttempt(kind, values || {});
        remember();
        busy = true;
        try {
            await initializeCurrent();
        } catch (_) {
            active.canRestart = !active?.reference;
            status(PHASE.FAILED, "Paystack couldn't be opened right now. Please try again.");
        } finally {busy=false;}
    }
    async function clearNativeListeners() {
        for (const listener of listeners.splice(0)) await listener.remove().catch(() => {});
    }
    function matchesReturn(url) {
        try {
            if (!active?.reference) return false;
            const actual = new URL(url);
            if (actual.protocol === 'cauldra:' && actual.hostname === 'payment-return') {
                return !actual.port && !actual.username && !actual.password &&
                    (actual.searchParams.get('reference') || actual.searchParams.get('trxref')) === active.reference;
            }
            if (!active.callback_url) return false;
            const expected = new URL(active.callback_url);
            return actual.origin === expected.origin && actual.pathname === expected.pathname &&
                (actual.searchParams.get('reference') || actual.searchParams.get('trxref')) === active.reference;
        } catch (_) {return false;}
    }
    async function launchNative() {
        if (!window.Capacitor?.isPluginAvailable?.('InAppBrowser')) throw new Error('native_checkout_unavailable');
        const browser = window.Capacitor.registerPlugin('InAppBrowser');
        await clearNativeListeners();
        listeners.push(await browser.addListener('browserClosed', onCancel));
        listeners.push(await browser.addListener('browserPageNavigationCompleted', async ({url}) => {
            if (!matchesReturn(url)) return;
            trace('PAYSTACK_RETURN_RECEIVED', {kind:active?.kind, phase:PHASE.RETURNED, native:true, hasReference:!!active?.reference, checkoutOpened:!!active?.checkoutOpened});
            await clearNativeListeners();
            await browser.close().catch(() => {}); providerOpen = false; await verify(true);
        }));
        const url = new URL(active.authorization_url);
        if (url.protocol !== 'https:' || url.hostname !== 'checkout.paystack.com' || url.username || url.password) throw new Error('invalid_checkout');
        trace('PAYSTACK_CHECKOUT_OPEN_REQUESTED', {kind:active.kind, phase:PHASE.CHECKOUT_READY, native:true, hasReference:true});
        providerOpen = true; el('payment-overlay').hidden = true;
        await browser.openInWebView({url:url.href, options:{showURL:false,showToolbar:true,closeButtonText:'Back to Cauldra',
            toolbarPosition:0,showNavigationButtons:true,clearCache:false,clearSessionCache:false,
            mediaPlaybackRequiresUserAction:true,leftToRight:false,
            android:{allowZoom:false,hardwareBack:true,pauseMedia:true},iOS:{allowOverScroll:true,enableViewportScale:false,allowInLineMediaPlayback:false,surpressIncrementalRendering:false,viewStyle:2,animationEffect:2,allowsBackForwardNavigationGestures:true}}});
        active.checkoutOpened = true;
        active.phase = PHASE.CHECKOUT_OPEN;
        remember();
        trace('PAYSTACK_CHECKOUT_OPENED', {kind:active.kind, phase:PHASE.CHECKOUT_OPEN, native:true, hasReference:true, checkoutOpened:true});
    }
    async function launch() {
        if (!active?.access_code || providerOpen) return;
        status('opening', 'Opening Paystack secure checkout…');
        try {
            if (native()) {
                await launchNative();
            } else {
                await loadSdk();
                popup = new window.PaystackPop();
                trace('PAYSTACK_CHECKOUT_OPEN_REQUESTED', {kind:active.kind, phase:PHASE.CHECKOUT_READY, native:false, hasReference:true});
                providerOpen = true;
                el('payment-overlay').hidden = true;
                popup.resumeTransaction(active.access_code, {
                    onSuccess: () => {providerOpen=false; trace('PAYSTACK_RETURN_RECEIVED',{kind:active?.kind,phase:PHASE.RETURNED,native:false,hasReference:!!active?.reference,checkoutOpened:true}); verify(true);},
                    onCancel,
                    onError: () => {providerOpen=false; el('payment-overlay').hidden=false; status(PHASE.FAILED,'Paystack could not load this payment. You can resume securely or check its status.');}
                });
                active.checkoutOpened = true;
                active.phase = PHASE.CHECKOUT_OPEN;
                remember();
                trace('PAYSTACK_CHECKOUT_OPENED', {kind:active.kind, phase:PHASE.CHECKOUT_OPEN, native:false, hasReference:true, checkoutOpened:true});
            }
        } catch (_) {
            providerOpen=false; el('payment-overlay').hidden=false;
            trace('PAYSTACK_CHECKOUT_OPEN_FAILED', {kind:active?.kind, phase:PHASE.FAILED, native:native(), hasReference:!!active?.reference, checkoutOpened:!!active?.checkoutOpened});
            status(PHASE.FAILED,'Paystack could not open on this device. You can resume the same secure checkout or check its status.');
        }
    }
    async function onCancel() {
        providerOpen=false; await clearNativeListeners(); el('payment-overlay').hidden=false;
        trace('PAYSTACK_RETURN_RECEIVED', {kind:active?.kind, phase:PHASE.CANCELLED, native:native(), hasReference:!!active?.reference, checkoutOpened:!!active?.checkoutOpened});
        status(PHASE.CANCELLED,"Payment verification wasn't completed.");
        await verify(false, true);
    }
    async function verify(poll = false, cancelled = false, setupOnly = false) {
        if (verifying || !active?.reference || active.ownerId !== owner()) return checking;
        verifying=true; el('payment-overlay').hidden=false;
        const attempt = active;
        checking=(async () => {
            if (!cancelled) status(PHASE.VERIFYING, setupOnly ? 'Checking the previous checkout setup…' : 'Confirming your payment securely…');
            trace('PAYSTACK_CONFIRM_REQUESTED', {kind:attempt.kind, phase:PHASE.VERIFYING, native:native(), hasReference:true, checkoutOpened:!!attempt.checkoutOpened});
            try {
                const response=await fetch(`${API_URL}${endpoints[attempt.kind][1]}`, {method:'POST',credentials:'include',
                    headers:headers(attempt.kind),body:JSON.stringify({reference:attempt.reference})});
                let data={}; try {data=await response.json();} catch (_) {}
                if (active !== attempt || attempt.ownerId !== owner()) return;
                if (data.status === 'cancelled') {
                    trace('PAYSTACK_CONFIRM_FAILED', {kind:attempt.kind, phase:PHASE.CANCELLED, httpStatus:response.status, hasReference:true, checkoutOpened:!!attempt.checkoutOpened});
                    attempt.reference = null; attempt.access_code = null; attempt.authorization_url = null; attempt.callback_url = null;
                    attempt.checkoutOpened = false; attempt.canRestart = true;
                    status(PHASE.CANCELLED, "Payment verification wasn't completed. You can try Paystack again.");
                    return;
                }
                if (response.status===202 || data.status==='pending') {
                    trace('PAYSTACK_CONFIRM_PENDING', {kind:attempt.kind, phase:PHASE.PENDING, httpStatus:response.status, hasReference:true, checkoutOpened:!!attempt.checkoutOpened});
                    // If checkout was never opened, this is provider reconciliation,
                    // not evidence the user submitted payment. Say exactly that.
                    status(PHASE.PENDING, attempt.checkoutOpened
                        ? 'Payment is still being confirmed. Do not pay again.'
                        : 'Paystack is still reconciling the checkout setup. No new charge has been started.');
                    if (poll) {clearTimeout(refreshTimer); refreshTimer=setTimeout(()=>verify(false),5000);}
                    return;
                }
                if (!response.ok || !['success','verified','trialing'].includes(data.status)) {
                    trace('PAYSTACK_CONFIRM_FAILED', {kind:attempt.kind, phase:PHASE.FAILED, httpStatus:response.status, hasReference:true, checkoutOpened:!!attempt.checkoutOpened});
                    if (data.status==='failed') {
                        attempt.reference = null; attempt.access_code = null; attempt.authorization_url = null; attempt.callback_url = null;
                        attempt.checkoutOpened = false; attempt.canRestart = true;
                    }
                    status(PHASE.FAILED, data.status==='requires_action'
                        ? 'Your card needs a billing review. Contact support before retrying.'
                        : (attempt.canRestart ? "Paystack couldn't complete this attempt. Please try again." : 'Payment could not be confirmed. Check again before paying again.'));
                    return;
                }
                trace('PAYSTACK_CONFIRM_SUCCESS', {kind:attempt.kind, phase:PHASE.VERIFIED, httpStatus:response.status, hasReference:true, checkoutOpened:!!attempt.checkoutOpened});
                forget();
                ['checkout','trial','onboarding'].forEach(k=>sessionStorage.removeItem(`cauldra_pending_${k}_reference`));
                active=null; await clearNativeListeners();
                el('payment-overlay').dataset.state = PHASE.VERIFIED;
                el('payment-status').textContent = attempt.kind==='method'?'Payment method updated.':'Payment successful';
                el('payment-spinner').hidden = true;
                const refund = data.refund_status === 'succeeded' ? 'Verification charge refund succeeded.'
                    : data.refund_status === 'failed' ? 'Verification charge refund failed. Contact support for a safe retry.'
                    : data.refund_status ? 'Verification charge refund is pending Paystack processing.' : '';
                if (refund) el('payment-explanation').textContent=refund;
                if (data.recurring_setup && data.recurring_setup!=='complete') el('payment-explanation').textContent='Payment verified. Automatic renewal is not confirmed; contact billing support.';
                if (attempt.kind==='onboarding') {
                    onboardingSelectedPlan=data.plan; onboardingSelectedInterval=data.billing_interval;
                    verifiedOnboardingReference=attempt.reference;
                    if (data.email && el('reg-owner-email')) el('reg-owner-email').value=data.email;
                    if (!publicPlanCatalog) await loadPublicPlanCatalog();
                    close(); openBusinessAuthModal(); switchBizAuthView('register');
                    showToast('Payment method verified. Complete your business details. '+refund,'success');
                } else {
                    await loadData(); openBillingModal(); await loadBillingPanel();
                    checkSubscriptionWarningForDashboard();
                }
                const url=new URL(location.href); url.searchParams.delete('reference'); url.searchParams.delete('trxref');
                history.replaceState(history.state,'',url.pathname+url.search+url.hash);
            } catch (_) {
                trace('PAYSTACK_CONFIRM_PENDING', {kind:attempt.kind, phase:PHASE.PENDING, hasReference:true, checkoutOpened:!!attempt.checkoutOpened});
                status(PHASE.PENDING, attempt.checkoutOpened
                    ? 'Payment verification is temporarily unavailable. Check again; do not pay again.'
                    : 'Paystack checkout status is temporarily unavailable. No new charge has been started.');
            } finally {verifying=false;}
        })();
        return checking;
    }
    async function resumeReturn(reference) {
        active=restore();
        if (!active) {
            const kind=reference?.startsWith('cauldra_onboard_')?'onboarding':reference?.startsWith('cauldra_trialcard_')?'trial':reference?.startsWith('cauldra_method_')?'method':'checkout';
            if (!reference || (kind!=='onboarding' && !authToken)) return;
            active={kind,reference,ownerId:owner(),phase:PHASE.RETURNED,checkoutOpened:true,canRestart:false};
        } else if (reference && active.reference && reference !== active.reference) {
            return; // never let an unrelated app link retarget an existing attempt
        } else if (reference) {
            active.reference = reference;
        }

        // A stored initialization record is not itself a provider return. This
        // path also runs during normal bootstrap when sessionStorage contains an
        // interrupted setup, so classify it before setting RETURNED.
        if (!reference) {
            show();
            if (!active.reference) {
                active.canRestart = true;
                status(PHASE.FAILED, "Paystack couldn't be opened right now. Please try again.");
                return;
            }
            await verify(false, false, !active.checkoutOpened);
            return;
        }

        active.checkoutOpened = true;
        active.phase = PHASE.RETURNED;
        remember();
        trace('PAYSTACK_RETURN_RECEIVED', {kind:active.kind, phase:PHASE.RETURNED, native:native(), hasReference:!!active.reference, checkoutOpened:true});
        show(); await verify(true);
    }
    document.addEventListener('DOMContentLoaded', () => {
        el('payment-close').addEventListener('click',close);
        el('payment-check').addEventListener('click',()=>verify(true));
        el('payment-resume').addEventListener('click',launch);
        el('payment-retry-init').addEventListener('click',retryInitialization);
        el('payment-overlay').addEventListener('keydown', event => {
            if (providerOpen || el('payment-overlay').hidden) return;
            if (event.key==='Escape') {event.preventDefault(); close();}
            if (event.key==='Tab') {
                const controls=[...el('payment-overlay').querySelectorAll('button:not([hidden]):not(:disabled)')];
                const first=controls[0], last=controls[controls.length-1];
                if (!first || !last) return;
                if (event.shiftKey && document.activeElement===first) {event.preventDefault(); last.focus();}
                else if (!event.shiftKey && document.activeElement===last) {event.preventDefault(); first.focus();}
            }
        });
    });
    window.addEventListener('online',()=>{if (active?.reference && !providerOpen) verify(false);});
    return {start,verify,resumeReturn,close};
})();
