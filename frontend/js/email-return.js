/* No Supabase session tokens or account details are persisted or displayed. */
(async () => {
    'use strict';
    // This page opens from an email, outside the app, so it carries its own copy
    // in the five launch languages. The language is the one the person last chose
    // in Cauldra on this browser (same origin), else the browser's, else English.
    const COPY = {
        en: {title: 'Email verification', checking: 'Checking your verification link…', help: 'You can return to the app where you started verification and check your status.',
            invalid: 'This link is not valid. Return to Cauldra and request a new verification email.', expired: 'This verification link has expired. Return to Cauldra and request a new email.',
            unusable: 'This verification link could not be used. Return to Cauldra and request a new email.', incomplete: 'Verification is not complete. Return to Cauldra and request a new email.',
            verifiedWeb: 'Your email has been verified. Returning to Cauldra…', returnBtn: 'Return to Cauldra', verifiedNative: 'Your email has been verified. Return to Cauldra to continue.',
            nativeHelp: 'If Cauldra does not open, install or open the app and choose “I’ve verified — check now”.', openBtn: 'Open Cauldra',
            failed: 'Verification could not be completed. Reload this page or return to Cauldra to check your status.', titleOk: 'Email verified', titleError: 'Verification not completed'},
        fr: {title: 'Vérification de l’e-mail', checking: 'Vérification de votre lien en cours…', help: 'Vous pouvez revenir dans l’application où vous avez commencé la vérification et consulter son état.',
            invalid: 'Ce lien n’est pas valide. Revenez dans Cauldra et demandez un nouvel e-mail de vérification.', expired: 'Ce lien de vérification a expiré. Revenez dans Cauldra et demandez un nouvel e-mail.',
            unusable: 'Ce lien de vérification n’a pas pu être utilisé. Revenez dans Cauldra et demandez un nouvel e-mail.', incomplete: 'La vérification n’est pas terminée. Revenez dans Cauldra et demandez un nouvel e-mail.',
            verifiedWeb: 'Votre e-mail a été vérifié. Retour vers Cauldra…', returnBtn: 'Revenir à Cauldra', verifiedNative: 'Votre e-mail a été vérifié. Revenez dans Cauldra pour continuer.',
            nativeHelp: 'Si Cauldra ne s’ouvre pas, installez ou ouvrez l’application et choisissez « J’ai vérifié — vérifier maintenant ».', openBtn: 'Ouvrir Cauldra',
            failed: 'La vérification n’a pas pu aboutir. Rechargez cette page ou revenez dans Cauldra pour consulter son état.', titleOk: 'E-mail vérifié', titleError: 'Vérification non terminée'},
        es: {title: 'Verificación del correo', checking: 'Comprobando tu enlace de verificación…', help: 'Puedes volver a la aplicación donde iniciaste la verificación y consultar su estado.',
            invalid: 'Este enlace no es válido. Vuelve a Cauldra y solicita un nuevo correo de verificación.', expired: 'Este enlace de verificación ha caducado. Vuelve a Cauldra y solicita un nuevo correo.',
            unusable: 'No se pudo usar este enlace de verificación. Vuelve a Cauldra y solicita un nuevo correo.', incomplete: 'La verificación no se ha completado. Vuelve a Cauldra y solicita un nuevo correo.',
            verifiedWeb: 'Tu correo se ha verificado. Volviendo a Cauldra…', returnBtn: 'Volver a Cauldra', verifiedNative: 'Tu correo se ha verificado. Vuelve a Cauldra para continuar.',
            nativeHelp: 'Si Cauldra no se abre, instala o abre la aplicación y elige «Ya lo verifiqué — comprobar ahora».', openBtn: 'Abrir Cauldra',
            failed: 'No se pudo completar la verificación. Recarga esta página o vuelve a Cauldra para consultar su estado.', titleOk: 'Correo verificado', titleError: 'Verificación no completada'},
        pt: {title: 'Verificação de e-mail', checking: 'Verificando seu link de verificação…', help: 'Você pode voltar ao aplicativo onde iniciou a verificação e consultar o status.',
            invalid: 'Este link não é válido. Volte ao Cauldra e solicite um novo e-mail de verificação.', expired: 'Este link de verificação expirou. Volte ao Cauldra e solicite um novo e-mail.',
            unusable: 'Não foi possível usar este link de verificação. Volte ao Cauldra e solicite um novo e-mail.', incomplete: 'A verificação não foi concluída. Volte ao Cauldra e solicite um novo e-mail.',
            verifiedWeb: 'Seu e-mail foi verificado. Voltando ao Cauldra…', returnBtn: 'Voltar ao Cauldra', verifiedNative: 'Seu e-mail foi verificado. Volte ao Cauldra para continuar.',
            nativeHelp: 'Se o Cauldra não abrir, instale ou abra o aplicativo e escolha “Já verifiquei — verificar agora”.', openBtn: 'Abrir o Cauldra',
            failed: 'Não foi possível concluir a verificação. Recarregue esta página ou volte ao Cauldra para consultar o status.', titleOk: 'E-mail verificado', titleError: 'Verificação não concluída'},
        ar: {title: 'التحقق من البريد الإلكتروني', checking: 'جارٍ التحقق من رابط التحقق…', help: 'يمكنك العودة إلى التطبيق الذي بدأت فيه التحقق ومراجعة الحالة.',
            invalid: 'هذا الرابط غير صالح. ارجع إلى Cauldra واطلب رسالة تحقق جديدة.', expired: 'انتهت صلاحية رابط التحقق هذا. ارجع إلى Cauldra واطلب رسالة جديدة.',
            unusable: 'تعذّر استخدام رابط التحقق هذا. ارجع إلى Cauldra واطلب رسالة جديدة.', incomplete: 'لم يكتمل التحقق. ارجع إلى Cauldra واطلب رسالة جديدة.',
            verifiedWeb: 'تم التحقق من بريدك الإلكتروني. جارٍ العودة إلى Cauldra…', returnBtn: 'العودة إلى Cauldra', verifiedNative: 'تم التحقق من بريدك الإلكتروني. ارجع إلى Cauldra للمتابعة.',
            nativeHelp: 'إذا لم يُفتح Cauldra، فثبّت التطبيق أو افتحه واختر «تحققت — تحقق الآن».', openBtn: 'فتح Cauldra',
            failed: 'تعذّر إكمال التحقق. أعد تحميل هذه الصفحة أو ارجع إلى Cauldra لمراجعة الحالة.', titleOk: 'تم التحقق من البريد الإلكتروني', titleError: 'لم يكتمل التحقق'},
    };
    const lang = (() => {
        try {
            const stored = String(localStorage.getItem('cauldra_language') || '').toLowerCase();
            if (COPY[stored]) return stored;
            const nav = String(navigator.language || '').slice(0, 2).toLowerCase();
            return COPY[nav] ? nav : 'en';
        } catch (_) { return 'en'; }
    })();
    const T = COPY[lang];
    const params = new URLSearchParams(location.search), challenge = params.get('challenge');
    // Links sent since CB-001 return a one-time session in the fragment (never
    // sent to any server by the browser); older links return a PKCE `code`.
    const fragment = new URLSearchParams(location.hash.slice(1));
    const code = params.get('code') || '', emailToken = fragment.get('access_token') || '';
    const linkError = fragment.get('error_code') || params.get('error_code') || fragment.get('error') || params.get('error') || '';
    const status = document.getElementById('email-return-status'), open = document.getElementById('email-return-open');
    // Visual state (spinner / check / warning) and translated static copy; both
    // are presentation only and never block the verification itself.
    const setState = (state) => {
        try {
            const main = document.querySelector('main'), icon = document.querySelector('.state-icon'), title = document.getElementById('email-return-title');
            if (main) main.dataset.state = state;
            if (icon) icon.textContent = state === 'success' ? '✓' : (state === 'error' ? '!' : '');
            if (icon && state === 'pending') icon.innerHTML = '<span class="spinner"></span>';
            if (title) title.textContent = state === 'success' ? T.titleOk : (state === 'error' ? T.titleError : T.title);
        } catch (_) {}
    };
    const fail = (message) => { status.textContent = message; setState('error'); };
    try {
        document.documentElement.lang = lang;
        document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
        document.title = 'Cauldra — ' + T.title;
        document.getElementById('email-return-title').textContent = T.title;
        document.getElementById('email-return-help').textContent = T.help;
        status.textContent = T.checking;
    } catch (_) {}
    const clearQuery = () => history.replaceState({}, document.title, location.pathname);
    if (params.get('purpose') !== 'onboarding' || !/^[a-f0-9]{64}$/.test(challenge || '')) {
        clearQuery();
        fail(T.invalid); return;
    }
    if (linkError && !code && !emailToken) {
        clearQuery();
        fail(linkError === 'otp_expired' ? T.expired : T.unusable);
        return;
    }
    try {
        const response = await fetch('/onboarding/email/verify/confirm', {method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({challenge_id:challenge, code, email_token:emailToken})});
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'verified') {
            // Preserve the one-time link proof across reload for transient server
            // and rate-limit failures. Definitive client errors are safe to clear.
            if (response.status < 500 && response.status !== 429) clearQuery();
            fail(typeof data.detail === 'string' && lang === 'en' ? data.detail : T.incomplete); return;
        }
        const target = new URL(data.return_target);
        if (data.platform === 'web') {
            const expected = new URL(data.expected_return_origin);
            if (target.protocol !== 'https:' || expected.protocol !== 'https:' || target.origin !== expected.origin
                    || target.username || target.password || expected.username || expected.password
                    || expected.pathname !== '/' || expected.search || expected.hash) throw new Error('invalid_return');
            target.searchParams.set('cauldra_email_verify','1'); target.searchParams.set('ev_purpose','onboarding');
            target.searchParams.set('challenge',challenge);
            status.textContent = T.verifiedWeb; setState('success');
            open.textContent = T.returnBtn; open.href = target.href; open.hidden = false;
            clearQuery();
            // Yield once so the real fallback anchor is usable even when an
            // embedded email browser suppresses the automatic navigation.
            setTimeout(() => location.replace(target.href), 0);
        } else if (['native_android','native_ios'].includes(data.platform) && target.href === 'cauldra://auth/email-verified') {
            target.searchParams.set('purpose','onboarding'); target.searchParams.set('challenge',challenge);
            status.textContent = T.verifiedNative; setState('success');
            document.getElementById('email-return-help').textContent = T.nativeHelp;
            open.textContent = T.openBtn; open.href = target.href; open.hidden = false;
            clearQuery();
            // User gesture works in email-client browsers that block auto-launch.
            setTimeout(() => location.href = target.href, 0);
        } else throw new Error('invalid_return');
    } catch (_) {
        // A transport failure deliberately leaves the captured query available
        // for reload; no token/session is persisted anywhere.
        fail(T.failed);
    }
})();
