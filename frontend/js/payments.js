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
    let active = null, busy = false, verifying = false, providerOpen = false;
    let popup = null, sdkPromise = null, listeners = [], returnFocus = null;
    let savedOverflow = '', refreshTimer = null, checking = null;
    const el = id => document.getElementById(id);
    const native = () => !!window.Capacitor?.isNativePlatform?.();
    const owner = () => typeof authToken === 'string' && authToken ? `${currentUserProfile?.business_id || ''}:${currentUserProfile?.id || ''}` : 'onboarding';
    function paymentDiagnostic(event, fields = {}) {
        const allowed = ['phase','native','httpStatus','referencePresent','accessCodePresent','providerHostname','pluginAvailable'];
        const safe = {};
        allowed.forEach(key => {if (Object.prototype.hasOwnProperty.call(fields,key)) safe[key]=fields[key];});
        console.info(`[CAULDRA_PAYMENT] ${event}`, safe);
    }
    function checkoutError(code, cause) {
        return Object.assign(new Error(code), {code, cause});
    }
    function validateInitialization(data) {
        if (!data || typeof data !== 'object' || !data.reference || !data.access_code || !data.authorization_url
                || !Number.isInteger(data.amount_kobo) || data.amount_kobo <= 0 || data.currency !== 'NGN'
                || !data.plan || !['monthly','annual'].includes(data.billing_interval) || !data.callback_url) {
            throw checkoutError('PAYSTACK_INIT_INVALID_PAYLOAD');
        }
        let provider;
        try {provider = new URL(data.authorization_url);} catch (_) {throw checkoutError('PAYSTACK_INIT_INVALID_PROVIDER_URL');}
        if (provider.protocol !== 'https:' || provider.hostname !== 'checkout.paystack.com'
                || provider.username || provider.password || provider.port) {
            throw checkoutError('PAYSTACK_INIT_INVALID_PROVIDER_URL');
        }
        return provider;
    }
    function remember() {
        if (!active) return;
        // Paystack access codes are browser-consumable checkout locators, not
        // secret keys or card data. Keep them only in this tab/app session so a
        // failed native launch can reopen the exact provider checkout/reference.
        const {kind, reference, key, values, ownerId, access_code, authorization_url,
            callback_url, amount_kobo, currency, plan, billing_interval} = active;
        sessionStorage.setItem(storageKey, JSON.stringify({kind, reference, key, values, ownerId,
            access_code, authorization_url, callback_url, amount_kobo, currency, plan, billing_interval}));
    }
    function restore() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
            if (saved && endpoints[saved.kind] && saved.ownerId === owner()) return saved;
        } catch (_) {}
        return null;
    }
    function status(phase, message) {
        el('payment-overlay').dataset.state = phase;
        el('payment-status').textContent = message;
        const retryable = ['pending', 'cancelled', 'error'].includes(phase);
        el('payment-check').hidden = !retryable || !active?.reference;
        el('payment-resume').hidden = !retryable || !active?.access_code;
        el('payment-spinner').hidden = !['initializing', 'verifying', 'opening'].includes(phase);
        el('payment-close').disabled = providerOpen;
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
    async function start(kind, values = {}) {
        if (busy || providerOpen || verifying) return;
        const saved = active || restore();
        if (saved && saved.ownerId === owner()) {
            active = saved; show();
            if (saved.access_code && saved.authorization_url) {
                status('pending', 'Your secure Paystack checkout is ready to resume.');
                await launch();
            } else if (saved.reference) {
                status('pending', 'An earlier payment is still open. Check its status before starting another.');
                await verify(false);
            } else {
                status('error', 'Paystack initialization did not create a checkout. You can retry safely.');
                el('payment-retry-init').hidden = false;
            }
            return;
        }
        busy = true;
        active = {kind, values, key: crypto.randomUUID(), ownerId: owner()};
        remember(); show();
        el('payment-plan').textContent = kind === 'method' ? 'Update payment method' : 'Secure checkout';
        el('payment-amount').textContent = 'Calculating securely…';
        status('initializing', 'Preparing your secure payment…');
        paymentDiagnostic('PAYSTACK_INIT_REQUESTED', {phase:'initializing',native:native(),referencePresent:false,accessCodePresent:false});
        try {
            const response = await fetch(`${API_URL}${endpoints[kind][0]}`, {
                method: 'POST', credentials: 'include', headers: headers(kind, active.key), body: JSON.stringify(values)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (data.detail?.reference) {active.reference = data.detail.reference; remember();}
                if (data.detail?.can_restart === true) {
                    active.reference = null;
                    active.key = crypto.randomUUID();
                    remember();
                }
                throw Object.assign(checkoutError('PAYSTACK_INIT_HTTP_FAILED'), {httpStatus:response.status,
                    canRestart:data.detail?.can_restart === true});
            }
            const provider = validateInitialization(data);
            Object.assign(active, data); remember();
            paymentDiagnostic('PAYSTACK_INIT_SUCCEEDED', {phase:'checkout_ready',native:native(),httpStatus:response.status,
                referencePresent:true,accessCodePresent:true,providerHostname:provider.hostname});
            el('payment-plan').textContent = `${data.plan || 'Payment method'} · ${data.billing_interval === 'annual' ? 'Annual' : 'Monthly'}`;
            el('payment-amount').textContent = new Intl.NumberFormat('en-NG', {style:'currency',currency:data.currency || 'NGN'}).format(data.amount_kobo / 100);
            el('payment-explanation').textContent = ['method','trial','onboarding'].includes(kind)
                ? 'This small card verification charge will be submitted for refund after verification. Paystack handles your payment details.'
                : 'Paystack handles your payment details. Your plan updates after Cauldra confirms the payment.';
            await launch();
        } catch (error) {
            paymentDiagnostic(error?.code || 'PAYSTACK_INIT_HTTP_FAILED', {phase:'error',native:native(),
                httpStatus:error?.httpStatus || null,referencePresent:!!active?.reference,accessCodePresent:!!active?.access_code});
            status('error', 'Secure checkout could not open. Check your connection and payment status before trying again.');
            el('payment-resume').hidden = !active?.access_code;
            el('payment-retry-init').hidden = !!active?.reference || !active;
        } finally { busy = false; }
    }
    async function retryInitialization() {
        if (busy || !active || active.reference) return;
        busy = true; status('initializing', 'Checking the same payment attempt…');
        try {
            const response = await fetch(`${API_URL}${endpoints[active.kind][0]}`, {method:'POST', credentials:'include',
                headers:headers(active.kind, active.key), body:JSON.stringify(active.values)});
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                if (data.detail?.reference) {active.reference=data.detail.reference; remember();}
                throw new Error('pending');
            }
            validateInitialization(data);
            Object.assign(active,data); remember(); await launch();
        } catch (error) {
            paymentDiagnostic(error?.code || 'PAYSTACK_INIT_HTTP_FAILED', {phase:'error',native:native(),
                httpStatus:error?.httpStatus || null,referencePresent:!!active?.reference,accessCodePresent:!!active?.access_code});
            status(active?.reference ? 'pending' : 'error', active?.reference
                ? 'The payment attempt has not been resolved. Check status or contact support; no new charge has been started.'
                : 'Paystack initialization did not create a checkout. You can retry safely.');
        }
        finally {busy=false;}
    }
    async function clearNativeListeners() {
        for (const listener of listeners.splice(0)) await listener.remove().catch(() => {});
    }
    function matchesReturn(url) {
        try {
            const actual = new URL(url), expected = new URL(active.callback_url);
            const trusted = (actual.origin === expected.origin && actual.pathname === expected.pathname)
                || (actual.protocol === 'cauldra:' && actual.hostname === 'payment-return');
            return trusted && (actual.searchParams.get('reference') || actual.searchParams.get('trxref')) === active.reference;
        } catch (_) {return false;}
    }
    async function launchNative() {
        const pluginAvailable = !!window.Capacitor?.isPluginAvailable?.('InAppBrowser');
        paymentDiagnostic('PAYSTACK_NATIVE_PLUGIN_CHECKED', {phase:'checkout_ready',native:true,
            pluginAvailable,referencePresent:!!active?.reference,accessCodePresent:!!active?.access_code});
        if (!pluginAvailable) throw checkoutError('PAYSTACK_NATIVE_PLUGIN_UNAVAILABLE');
        const browser = window.Capacitor.registerPlugin('InAppBrowser');
        await clearNativeListeners();
        try {
            listeners.push(await browser.addListener('browserClosed', onCancel));
            listeners.push(await browser.addListener('browserPageNavigationCompleted', async ({url}) => {
                if (!matchesReturn(url)) return;
                paymentDiagnostic('PAYSTACK_PROVIDER_RETURNED', {phase:'returned',native:true,referencePresent:true,accessCodePresent:true});
                await clearNativeListeners();
                await browser.close().catch(() => {}); providerOpen = false; await verify(true);
            }));
            if (window.Capacitor.isPluginAvailable('App')) {
                const app = window.Capacitor.registerPlugin('App');
                listeners.push(await app.addListener('appUrlOpen', async ({url}) => {
                    if (matchesReturn(url)) {await clearNativeListeners(); await browser.close().catch(() => {}); providerOpen=false; await verify(true);}
                }));
                listeners.push(await app.addListener('appStateChange', ({isActive}) => {if (isActive && active?.reference && !providerOpen) verify(false);}));
            }
        } catch (error) {
            await clearNativeListeners();
            throw checkoutError('PAYSTACK_NATIVE_LISTENER_FAILED', error);
        }
        const url = new URL(active.authorization_url);
        if (url.protocol !== 'https:' || url.hostname !== 'checkout.paystack.com' || url.username || url.password || url.port) {
            throw checkoutError('PAYSTACK_INIT_INVALID_PROVIDER_URL');
        }
        paymentDiagnostic('PAYSTACK_NATIVE_OPEN_REQUESTED', {phase:'opening',native:true,pluginAvailable:true,
            referencePresent:true,accessCodePresent:true,providerHostname:url.hostname});
        try {
            await browser.openInWebView({url:url.href, options:{showURL:false,showToolbar:true,closeButtonText:'Back to Cauldra',
                toolbarPosition:0,showNavigationButtons:true,clearCache:false,clearSessionCache:false,
                mediaPlaybackRequiresUserAction:true,leftToRight:false,
                android:{allowZoom:false,hardwareBack:true,pauseMedia:true},iOS:{allowOverScroll:true,enableViewportScale:false,allowInLineMediaPlayback:false,surpressIncrementalRendering:false,viewStyle:2,animationEffect:2,allowsBackForwardNavigationGestures:true}}});
        } catch (error) {
            await clearNativeListeners();
            throw checkoutError('PAYSTACK_NATIVE_OPEN_FAILED', error);
        }
        paymentDiagnostic('PAYSTACK_NATIVE_OPENED', {phase:'checkout_open',native:true,pluginAvailable:true,
            referencePresent:true,accessCodePresent:true,providerHostname:url.hostname});
    }
    async function launch() {
        if (!active?.access_code || providerOpen) return;
        status('opening', 'Opening Paystack secure checkout…');
        try {
            if (native()) {
                providerOpen = true; el('payment-overlay').hidden=true; await launchNative();
            } else {
                try {await loadSdk();} catch (error) {throw checkoutError('PAYSTACK_WEB_SDK_FAILED', error);}
                popup = new window.PaystackPop();
                providerOpen = true;
                // Remove Cauldra's modal from the accessibility tree while the
                // provider owns focus. Never trap keystrokes over its frame.
                el('payment-overlay').hidden = true;
                popup.resumeTransaction(active.access_code, {
                    onSuccess: () => {providerOpen=false; paymentDiagnostic('PAYSTACK_PROVIDER_RETURNED', {
                        phase:'returned',native:false,referencePresent:!!active?.reference,accessCodePresent:!!active?.access_code}); verify(true);},
                    onCancel,
                    onError: () => {providerOpen=false; paymentDiagnostic('PAYSTACK_WEB_SDK_FAILED', {
                        phase:'error',native:false,referencePresent:!!active?.reference,accessCodePresent:!!active?.access_code});
                        el('payment-overlay').hidden=false; status('error','Paystack could not load this payment. You can check its status or resume securely.');}
                });
            }
        } catch (error) {
            providerOpen=false; el('payment-overlay').hidden=false;
            paymentDiagnostic(error?.code || (native() ? 'PAYSTACK_NATIVE_OPEN_FAILED' : 'PAYSTACK_WEB_SDK_FAILED'), {
                phase:'error',native:native(),referencePresent:!!active?.reference,
                accessCodePresent:!!active?.access_code,pluginAvailable:native() ? !!window.Capacitor?.isPluginAvailable?.('InAppBrowser') : null});
            status('error','Secure checkout is unavailable on this device. Your payment has not been marked as failed.');
        }
    }
    async function onCancel() {
        providerOpen=false; await clearNativeListeners(); el('payment-overlay').hidden=false;
        status('cancelled','Checkout closed. Your plan stays unchanged until payment is verified.');
        await verify(false, true);
    }
    async function verify(poll = false, cancelled = false) {
        if (verifying || !active?.reference || active.ownerId !== owner()) return checking;
        verifying=true; el('payment-overlay').hidden=false;
        const attempt = active;
        checking=(async () => {
            if (!cancelled) status('verifying','Confirming your payment securely…');
            paymentDiagnostic('PAYSTACK_CONFIRM_REQUESTED', {phase:'verifying',native:native(),
                referencePresent:true,accessCodePresent:!!attempt.access_code});
            try {
                const response=await fetch(`${API_URL}${endpoints[attempt.kind][1]}`, {method:'POST',credentials:'include',
                    headers:headers(attempt.kind),body:JSON.stringify({reference:attempt.reference})});
                const data=await response.json();
                if (active !== attempt || attempt.ownerId !== owner()) return;
                if (response.status===202 || data.status==='pending') {
                    status(cancelled?'cancelled':'pending',cancelled?'Checkout closed. Payment is unconfirmed; you can resume or check again.':'Payment is still being confirmed. Do not pay again.');
                    if (poll) {clearTimeout(refreshTimer); refreshTimer=setTimeout(()=>verify(false),5000);}
                    return;
                }
                if (!response.ok || !['success','verified','trialing'].includes(data.status)) {
                    paymentDiagnostic('PAYSTACK_CONFIRM_FAILED', {phase:'error',native:native(),httpStatus:response.status,
                        referencePresent:true,accessCodePresent:!!attempt.access_code});
                    if (data.status==='failed') {sessionStorage.removeItem(storageKey); active=null;}
                    status('error', data.status==='requires_action' ? 'Your card needs a billing review. Contact support before retrying. Your previous payment details have been retained.' : 'Payment could not be confirmed. Check again or contact support before paying again.');
                    return;
                }
                sessionStorage.removeItem(storageKey);
                paymentDiagnostic('PAYSTACK_CONFIRM_SUCCEEDED', {phase:'verified',native:native(),httpStatus:response.status,
                    referencePresent:true,accessCodePresent:!!attempt.access_code});
                ['checkout','trial','onboarding'].forEach(k=>sessionStorage.removeItem(`cauldra_pending_${k}_reference`));
                active=null; await clearNativeListeners();
                status('success', attempt.kind==='method'?'Payment method updated.':'Payment successful');
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
                    // Both feature permissions and AI entitlement refresh from
                    // the server; preserve the user's Billing destination.
                    await loadData(); openBillingModal(); await loadBillingPanel();
                    checkSubscriptionWarningForDashboard();
                }
                const url=new URL(location.href); url.searchParams.delete('reference'); url.searchParams.delete('trxref');
                history.replaceState(history.state,'',url.pathname+url.search+url.hash);
            } catch (_) {
                paymentDiagnostic('PAYSTACK_CONFIRM_FAILED', {phase:'pending',native:native(),
                    referencePresent:true,accessCodePresent:!!attempt.access_code});
                status('pending','Payment verification is unavailable. Check your connection and try checking again; do not pay again.');
            }
            finally {verifying=false;}
        })();
        return checking;
    }
    async function resumeReturn(reference) {
        active=restore();
        if (!active) {
            let kind=reference?.startsWith('cauldra_onboard_')?'onboarding':reference?.startsWith('cauldra_trialcard_')?'trial':reference?.startsWith('cauldra_method_')?'method':'checkout';
            if (!reference || (kind!=='onboarding' && !authToken)) return;
            active={kind,reference,ownerId:owner()};
        }
        // The URL is only a routing hint; confirmation is always tenant-scoped.
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
                if (event.shiftKey && document.activeElement===first) {event.preventDefault(); last.focus();}
                else if (!event.shiftKey && document.activeElement===last) {event.preventDefault(); first.focus();}
            }
        });
    });
    window.addEventListener('online',()=>{if (active?.reference && !providerOpen) verify(false);});
    return {start,verify,resumeReturn,close};
})();
