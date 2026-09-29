// NOTIF-PUSH-001 / NOTIF-PUSH-002 frontend wiring (static checks on frontend/js/app.js and sw.js).
// The device side of push ownership: sign-out tells the server which registration is this
// device's and then unregisters it; a dead session or a guest start-up releases the device;
// every sign-in re-registers the signed-in user; the Android app uses the native plugin.
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.join(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'frontend', 'js', 'app.js'), 'utf8');
const sw = fs.readFileSync(path.join(root, 'frontend', 'sw.js'), 'utf8');
const manifest = fs.readFileSync(path.join(root, 'android', 'app', 'src', 'main', 'AndroidManifest.xml'), 'utf8');
const capConfig = JSON.parse(fs.readFileSync(path.join(root, 'capacitor.config.json'), 'utf8'));

function body(name) {
    const start = app.search(new RegExp(`(async\\s+)?function ${name}\\(`));
    assert.ok(start >= 0, `${name} exists`);
    // The body starts at the first "{" after the parameter list's ")" (a default like
    // `options = {}` must not be taken for the body).
    const open = /\)\s*\{/g; open.lastIndex = start; const m = open.exec(app);
    let depth = 0, i = m.index + m[0].length - 1;
    for (let j = i; j < app.length; j++) {
        if (app[j] === '{') depth++;
        else if (app[j] === '}' && --depth === 0) return app.slice(start, j + 1);
    }
    throw new Error(`unbalanced ${name}`);
}

const checks = [];
function check(label, fn) { fn(); checks.push(label); }

check('sign-out sends this device\'s registration to /auth/logout, then releases the device', () => {
    const b = body('handleSignOut');
    const identity = b.indexOf('currentDevicePushIdentity()');
    const logout = b.indexOf('fetch(`${API_URL}/auth/logout`');  // the call itself, not the comments naming it
    const release = b.indexOf('releaseDevicePushOnSignOut()');
    assert.ok(identity > 0 && logout > identity && release > logout, 'identity -> logout -> release');
    assert.match(b.slice(logout, release), /body:\s*JSON\.stringify\(devicePushIdentity\)/);
});

check('a dead session releases the device', () => {
    assert.match(body('handleAuthenticationFailure'), /releaseDevicePushOnSignOut\(\)/);
});

check('a guest start-up releases the device', () => {
    const b = body('runLoadData');
    // The branch that finishes start-up as a guest (empty data, guest header), not the earlier session re-check.
    const guest = b.indexOf('productsSuppliersReady = true; warehousesReady = true;');
    assert.ok(guest > 0, 'guest branch');
    const branch = b.slice(guest, b.indexOf('return;', guest));
    assert.match(branch, /releaseDevicePushOnSignOut\(\)/);
});

check('every sign-in re-registers the signed-in user; the password change does too', () => {
    assert.match(body('startNotificationPolling'), /setTimeout\(syncPushRegistrationForSession, \d+\)/);
    assert.match(app, /closePasswordChangeDialog\(\);\s*showToast\(t\("auth\.passwordUpdatedSuccess"\), "success"\);[\s\S]{0,300}syncPushRegistrationForSession/);
    const sync = body('syncPushRegistrationForSession');
    assert.match(sync, /pushRegistrationAllowed\(\)/);
    assert.match(sync, /registerNativePush\(false\)/);
    assert.match(sync, /subscribeToPushNotifications\(\{ silent: true \}\)/);
});

check('registration never happens without a signed-in, password-settled business session', () => {
    assert.match(body('pushRegistrationAllowed'), /authToken && hasAuthenticatedBusinessContext\(\) && !currentUserProfile\?\.must_change_password/);
    const listener = body('initializeNativePush');
    assert.match(listener, /"registration"[\s\S]*pushRegistrationAllowed\(\)[\s\S]*\/push\/native\/register/);
});

check('registration calls carry the sign-in cookie', () => {
    assert.match(body('postPushRegistration'), /credentials:\s*"include"/);
    assert.match(body('subscribeToPushNotifications'), /postPushRegistration\("\/push\/subscribe"/);
});

check('web push stays browser-only; the Android app uses the PushNotifications plugin', () => {
    assert.match(body('pushNotificationsSupported'), /!isNativeAppShell\(\)/);
    assert.match(body('nativePushPlugin'), /isPluginAvailable\?\.\('PushNotifications'\)/);
    assert.match(body('subscribeToPushNotifications'), /if \(nativePushPlugin\(\)\)/);
    assert.match(body('releaseDevicePushOnSignOut'), /push\.unregister\(\)/);
    assert.match(body('releaseDevicePushOnSignOut'), /subscription\.unsubscribe\(\)/);
});

check('native taps open the notification\'s deep link, or after the next sign-in', () => {
    assert.match(body('initializeNativePush'), /"pushNotificationActionPerformed"[\s\S]*openPushDeepLink\(action\?\.notification\?\.data\?\.deep_link/);
    assert.match(body('openPushDeepLink'), /pendingPushDeepLink = \{ deepLink, at: Date\.now\(\) \}/);
});

check('Android: permission, channel, Cauldra icon and foreground display are configured', () => {
    assert.match(manifest, /android\.permission\.POST_NOTIFICATIONS/);
    assert.match(manifest, /default_notification_icon"\s*android:resource="@drawable\/ic_stat_cauldra"/);
    assert.match(manifest, /default_notification_channel_id"\s*android:value="cauldra_alerts"/);
    assert.match(app, /NATIVE_PUSH_CHANNEL_ID = "cauldra_alerts"/);
    assert.deepStrictEqual(capConfig.plugins.PushNotifications.presentationOptions.includes('alert'), true);
    for (const d of ['mdpi', 'hdpi', 'xhdpi', 'xxhdpi', 'xxxhdpi']) {
        assert.ok(fs.existsSync(path.join(root, 'android', 'app', 'src', 'main', 'res', `drawable-${d}`, 'ic_stat_cauldra.png')), `icon ${d}`);
    }
});

check('the Web Push badge is the one-colour Cauldra mark', () => {
    assert.match(sw, /badge: "\/assets\/notification-badge-96\.png"/);
    assert.ok(fs.existsSync(path.join(root, 'frontend', 'assets', 'notification-badge-96.png')));
});

for (const c of checks) console.log(`PASS ${c}`);
console.log(`All ${checks.length} push-ownership frontend checks passed`);
