'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
const verifier = fs.readFileSync(path.join(root, 'scripts/verify-native-bundle.js'), 'utf8');

const forbidden = 'Email return could not initialize. Reopen Cauldra to retry.';
assert(!app.includes(forbidden), 'ordinary production startup must not contain the old email error UI');
assert(verifier.includes(forbidden), 'native parity verifier must retain the forbidden-string regression assertion');

async function main() {
    const startup = app.match(/evInitializeNativeReturn\(\)\.catch\([^;]+;/)?.[0] || '';
    assert(startup.includes('console.warn'), 'normal startup listener failure must be diagnostic-only');
    assert(!startup.includes('showToast'), 'fresh, authenticated, and resumed ordinary startup must not show email error UI');

    const nativeInitializer = app.match(/async function evInitializeNativeReturn\(\) \{[\s\S]+?\n        \}/)?.[0] || '';
    for (const marker of ['appUrlOpen', 'getLaunchUrl', "link.protocol !== 'cauldra:'", "link.pathname !== '/email-verified'", "link.searchParams.get('purpose') !== 'onboarding'", 'evResumeChallenge']) {
        assert(nativeInitializer.includes(marker), `working native callback marker missing: ${marker}`);
    }
    assert(nativeInitializer.includes('{contextualReturn:true}'), 'recognized native returns must request contextual recovery');

    // Execute the actual production initializer with a deterministic native
    // App-plugin mock. Ordinary launch/resume must be silent, while the exact
    // recognized callback must invoke the real routing dependency.
    const recovery = app.match(/function evShowReturnRecovery\(message\) \{[\s\S]+?\n        \}/)?.[0] || '';
    for (const marker of ['openBusinessAuthModal()', "switchBizAuthView('payment-email')", "getElementById('ev-state-4-msg')", 'evShowState(4)']) {
        assert(recovery.includes(marker), `recognized return failure recovery missing: ${marker}`);
    }
    assert(recovery.includes('Email verification return:'), 'recognized return error must be contextual');

    const viewports = [[375, 812], [768, 1024], [1024, 768], [1366, 768]];
    for (const [width, height] of viewports) {
        const listeners = {}, resumeCalls = [], verificationChecks = [], recoveryCalls = [];
        const context = {
            URL,
            evChallengeId: null,
            checkEmailVerification: (...args) => verificationChecks.push(args),
            evResumeChallenge: (...args) => resumeCalls.push(args),
            openBusinessAuthModal: () => recoveryCalls.push('open'),
            switchBizAuthView: view => recoveryCalls.push(`view:${view}`),
            evShowState: state => recoveryCalls.push(`state:${state}`),
            document: {getElementById: id => id === 'ev-state-4-msg' ? {set textContent(value) { recoveryCalls.push(`message:${value}`); }} : null},
            window: {innerWidth: width, innerHeight: height, Capacitor: {
                isNativePlatform: () => true,
                isPluginAvailable: name => name === 'App',
                registerPlugin: () => ({
                    addListener: async (name, handler) => { listeners[name] = handler; },
                    getLaunchUrl: async () => null,
                }),
            }},
        };
        vm.runInNewContext(`${nativeInitializer}; ${recovery}; this.initialize = evInitializeNativeReturn; this.recover = evShowReturnRecovery;`, context);
        await context.initialize();
        await listeners.appStateChange({isActive: true});
        assert.equal(verificationChecks.length, 0, `${width}x${height}: ordinary resume must remain silent`);
        await listeners.appUrlOpen({url: 'https://example.com/ordinary'});
        assert.equal(resumeCalls.length, 0, `${width}x${height}: ordinary URL must remain silent`);
        const challenge = 'a'.repeat(64);
        await listeners.appUrlOpen({url: `cauldra://auth/email-verified?purpose=onboarding&challenge=${challenge}`});
        assert.equal(resumeCalls.length, 1, `${width}x${height}: recognized return must execute its handler`);
        assert.equal(resumeCalls[0][0], challenge);
        assert.equal(resumeCalls[0][1].contextualReturn, true);
        context.recover('Please request a new verification email.');
        assert.deepEqual(recoveryCalls.slice(0, 2), ['open', 'view:payment-email']);
        assert(recoveryCalls[2].startsWith('message:Email verification return:'));
        assert.equal(recoveryCalls[3], 'state:4');
    }

    console.log('PASS: ordinary startup is silent and callback/recovery paths execute at 375x812, 768x1024, 1024x768, and 1366x768.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
