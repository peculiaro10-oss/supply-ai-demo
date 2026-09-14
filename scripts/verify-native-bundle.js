"use strict";
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const root = path.resolve(__dirname, '..');
const buildTarget = require('./build-target-config');
const NL = '\n';
const hash = p => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
function inventory(dir, prefix = '') {
    const result = {};
    for (const entry of fs.readdirSync(dir, {withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))) {
        const rel = prefix + entry.name, file = path.join(dir, entry.name);
        if (entry.isSymbolicLink()) throw new Error('Symlink not allowed in frontend bundle: ' + rel);
        if (entry.isDirectory()) Object.assign(result, inventory(file, rel + '/'));
        else result[rel] = hash(file);
    }
    return result;
}
// BUILD-001: a manifest describes a bundle built for ONE declared backend target.
// The expected bytes of js/build-target.js are re-derived here from the tracked
// registry -- never read back from the artifact being checked.
function sourceManifest(target) {
    const resolved = (target && typeof target === 'object') ? buildTarget.resolveTarget(target.name) : buildTarget.resolveTarget(target);
    const files = inventory(path.join(root,'frontend'));
    const sourceBytes = buildTarget.renderTargetFile(buildTarget.sourceTargetName());
    if (files[buildTarget.EMITTED_FILE] !== crypto.createHash('sha256').update(sourceBytes).digest('hex')) {
        throw new Error('frontend/' + buildTarget.EMITTED_FILE + ' has drifted from scripts/build-targets.json.' + NL +
            'It is generated, not hand-written: restore it with `npm run sync:build-target`.');
    }
    files[buildTarget.EMITTED_FILE] = crypto.createHash('sha256').update(buildTarget.renderTargetFile(resolved)).digest('hex');
    const html = fs.readFileSync(path.join(root,'frontend/index.html'),'utf8');
    for (const match of html.matchAll(/(?:src|href)="(\/[^"#]+)"/g)) {
        const asset = match[1].slice(1).split('?')[0];
        if (!files[asset]) throw new Error('Missing frontend shell asset: '+asset);
    }
    const version = JSON.parse(fs.readFileSync(path.join(root,'package.json'))).version;
    const configHash = hash(path.join(root,'capacitor.config.json'));
    const dependencyHash = hash(path.join(root,'package-lock.json'));
    const buildId = crypto.createHash('sha256').update(JSON.stringify({version, target:resolved.name, configHash, dependencyHash, files})).digest('hex');
    return {version, target:resolved.name, apiBaseUrl:resolved.apiBaseUrl, sourceVersion:buildId, buildId, configHash, dependencyHash, files};
}
// The target is an input, not a belief. When it is not supplied explicitly (Gradle
// calls this with no arguments) it is read from the generated manifest and then
// re-validated against the tracked registry, so an artifact cannot assert its own
// correctness: the name must be declared in scripts/build-targets.json, and the
// emitted file must match the bytes this repository renders for that name.
function targetFromGeneratedManifest() {
    const file = path.join(root,'www','build-manifest.json');
    let declared;
    try { declared = JSON.parse(fs.readFileSync(file,'utf8')).target; }
    catch (_) {
        throw new Error('www/build-manifest.json missing or unreadable, so the build target is unknown.' + NL +
            'Run: npm run build:www -- --target=<' + buildTarget.targetNames().join('|') + '>');
    }
    if (!declared) {
        throw new Error('www/build-manifest.json declares no build target.' + NL +
            'This bundle predates BUILD-001; rebuild it with an explicit --target.');
    }
    return buildTarget.resolveTarget(declared);
}

function verify(native = true, target) {
    const resolved = (target === undefined || target === null || target === '')
        ? targetFromGeneratedManifest()
        : buildTarget.resolveTarget(typeof target === 'object' ? target.name : target);
    const expected = sourceManifest(resolved), failures = [];
    for (const rel of ['index.html',buildTarget.EMITTED_FILE,'js/app.js','js/payments.js','js/offline.js','css/base.css','css/payments.css','css/offline.css','sw.js'])
        if (!expected.files[rel]) failures.push('frontend/' + rel + ' missing');
    const dirs = ['www'];
    if (native) dirs.push('android/app/src/main/assets/public');
    for (const dir of dirs) {
        const target = path.join(root,dir);
        for (const [file,digest] of Object.entries(expected.files)) {
            const output = path.join(target,file);
            if (!fs.existsSync(output) || hash(output) !== digest) failures.push(dir+'/'+file+' missing or stale');
        }
        try {
            const manifest = JSON.parse(fs.readFileSync(path.join(target,'build-manifest.json'),'utf8'));
            if (JSON.stringify(manifest) !== JSON.stringify(expected)) failures.push(dir+'/build-manifest.json stale');
            const actual = inventory(target);
            for (const file of Object.keys(actual)) {
                if (expected.files[file] || file === 'build-manifest.json') continue;
                if (dir !== 'www' && /^(capacitor\.js|capacitor\.js\.map|cordova\.js|cordova_plugins\.js|plugins\/)/.test(file)) continue;
                failures.push(dir+'/'+file+' unexpected stale output');
            }
        } catch (_) { failures.push(dir+'/build-manifest.json missing or unreadable'); }
    }
    // BUILD-001: the emitted override must match the declared target byte-for-byte in
    // every generated tree. Asserted explicitly so a wrong-target build reports the
    // target it actually carries instead of a nondescript stale-file hash mismatch.
    for (const dir of dirs) {
        const file = path.join(root,dir,buildTarget.EMITTED_FILE);
        if (!fs.existsSync(file)) { failures.push(dir+'/'+buildTarget.EMITTED_FILE+' missing; rebuild with an explicit --target'); continue; }
        if (!fs.readFileSync(file).equals(buildTarget.renderTargetFile(resolved))) {
            failures.push(dir+'/'+buildTarget.EMITTED_FILE+' does not declare build target ' + resolved.name + ' (' + (resolved.apiBaseUrl||'same origin') + ')');
        }
    }
    // An override that loads after app.js is an override that never applied.
    for (const dir of ['frontend',...dirs]) {
        const file = path.join(root,dir,'index.html');
        if (!fs.existsSync(file)) continue;
        const html = fs.readFileSync(file,'utf8');
        const overrideAt = html.indexOf('src="/'+buildTarget.EMITTED_FILE+'"');
        const appAt = html.indexOf('src="/js/app.js"');
        if (overrideAt < 0) failures.push(dir+'/index.html must load /'+buildTarget.EMITTED_FILE);
        else if (appAt >= 0 && overrideAt > appAt) failures.push(dir+'/index.html must load /'+buildTarget.EMITTED_FILE+' BEFORE /js/app.js');
    }

    // A concrete regression assertion on source AND both generated shells.
    for (const dir of ['frontend',...dirs]) {
        const file = path.join(root,dir,'index.html');
        if (!fs.existsSync(file)) continue;
        const tag = fs.readFileSync(file,'utf8').match(/<[^>]+id="header-global-search-wrap"[^>]*>/)?.[0] || '';
        if (!/class="[^"]*\bhidden\b/.test(tag)) failures.push(dir+'/index.html guest search must start hidden');
    }
    const forbiddenEmailStartupError = 'Email return could not initialize. Reopen Cauldra to retry.';
    for (const dir of ['frontend',...dirs]) {
        const file = path.join(root,dir,'js/app.js');
        if (fs.existsSync(file) && fs.readFileSync(file,'utf8').includes(forbiddenEmailStartupError)) {
            failures.push(dir+'/js/app.js contains forbidden ordinary-startup email return error UI');
        }
    }
    for (const dir of ['frontend',...dirs]) {
        const appFile = path.join(root,dir,'js/app.js');
        const offlineFile = path.join(root,dir,'js/offline.js');
        const cssFile = path.join(root,dir,'css/offline.css');
        if (!fs.existsSync(appFile) || !fs.existsSync(offlineFile) || !fs.existsSync(cssFile)) continue;
        const appSource = fs.readFileSync(appFile,'utf8');
        const offlineSource = fs.readFileSync(offlineFile,'utf8');
        const offlineCss = fs.readFileSync(cssFile,'utf8');
        const enabledStatus = appSource.match(/<p[^>]*>Enabled on this device<\/p>/)?.[0] || '';
        if (!enabledStatus.includes('text-primary') || enabledStatus.includes('text-success')) {
            failures.push(dir+'/js/app.js Offline Access enabled status must use Cauldra primary, never success green');
        }
        for (const marker of ['id="offline-opt-in-enable" class="offline-primary"','id="offline-pin-unlock" class="offline-primary"','type="submit" class="offline-primary">Enable on this device','type="submit" class="offline-primary">Enable Biometrics','id="offline-remove-confirm" class="offline-danger"']) {
            if (!offlineSource.includes(marker)) failures.push(dir+'/js/offline.js missing Offline action hierarchy marker: '+marker);
        }
        if (!/\.offline-settings-actions button[^}]*background:#0d1322/i.test(offlineCss)) failures.push(dir+'/css/offline.css management actions must retain neutral Cauldra surface');
        if (!/\.offline-dialog \.offline-primary[^}]*background:#436bee/i.test(offlineCss)) failures.push(dir+'/css/offline.css primary Offline action must use Cauldra primary');
        if (!/\.offline-dialog \.offline-danger[^}]*background:#d94141/i.test(offlineCss)) failures.push(dir+'/css/offline.css destructive Offline action must use Cauldra danger');
        if (/(?:green|teal|emerald|cyan|lime)|#(?:0f|10b|14b|16a|22c)[0-9a-f]{3,6}/i.test(offlineCss)) failures.push(dir+'/css/offline.css contains a forbidden Offline green/teal color family');
    }
    if (native) {
        try {
            const assets = path.join(root,'android/app/src/main/assets');
            const config = JSON.parse(fs.readFileSync(path.join(root,'capacitor.config.json')));
            const packaged = JSON.parse(fs.readFileSync(path.join(assets,'capacitor.config.json')));
            for (const key of ['appId','appName','server','plugins']) {
                if (JSON.stringify(config[key]) !== JSON.stringify(packaged[key])) failures.push('Android Capacitor config drift: '+key);
            }
            const plugins = JSON.parse(fs.readFileSync(path.join(assets,'capacitor.plugins.json')));
            for (const name of ['@capacitor/app','@capacitor/inappbrowser']) {
                if (!plugins.some(plugin => plugin.pkg === name)) failures.push('Missing native plugin '+name+'; run npm ci before sync');
            }
            const javaRoot = path.join(root,'android/app/src/main/java/com/example/cauldra');
            const mainActivity = fs.readFileSync(path.join(javaRoot,'MainActivity.java'),'utf8');
            const biometricPlugin = fs.readFileSync(path.join(javaRoot,'CauldraBiometricPlugin.java'),'utf8');
            const androidManifest = fs.readFileSync(path.join(root,'android/app/src/main/AndroidManifest.xml'),'utf8');
            const appGradle = fs.readFileSync(path.join(root,'android/app/build.gradle'),'utf8');
            if (!mainActivity.includes('registerPlugin(CauldraBiometricPlugin.class)')) failures.push('CauldraBiometric native plugin is not registered');
            for (const marker of ['@CapacitorPlugin(name = "CauldraBiometric")','BiometricPrompt.CryptoObject','AndroidKeyStore','setUserAuthenticationRequired(true)']) {
                if (!biometricPlugin.includes(marker)) failures.push('CauldraBiometricPlugin missing security marker: '+marker);
            }
            if (!androidManifest.includes('android.permission.USE_BIOMETRIC')) failures.push('Android biometric permission missing');
            if (!appGradle.includes('androidx.biometric:biometric:1.1.0')) failures.push('Stable AndroidX Biometric dependency missing');
        } catch (_) { failures.push('Android Capacitor config/plugin metadata missing'); }
    }
    if (failures.length) throw new Error(failures.join('\n')+'\nRun npm run android:prepare from the repository root.');
    console.log('PASS bundle parity: ' + expected.buildId + '  [target ' + resolved.name + ' -> ' + (resolved.apiBaseUrl || 'same origin') + ']');
}
module.exports = {sourceManifest, verify};
if (require.main === module) {
    const flag = process.argv.find(a => a.startsWith('--target='));
    try {verify(!process.argv.includes('--www-only'), flag ? flag.slice('--target='.length) : undefined);}
    catch (error) {console.error(error.message); process.exitCode=1;}
}
