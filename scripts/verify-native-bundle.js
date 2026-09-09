"use strict";
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const root = path.resolve(__dirname, '..');
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
function sourceManifest() {
    const files = inventory(path.join(root,'frontend'));
    const html = fs.readFileSync(path.join(root,'frontend/index.html'),'utf8');
    for (const match of html.matchAll(/(?:src|href)="(\/[^"#]+)"/g)) {
        const asset = match[1].slice(1).split('?')[0];
        if (!files[asset]) throw new Error('Missing frontend shell asset: '+asset);
    }
    const version = JSON.parse(fs.readFileSync(path.join(root,'package.json'))).version;
    const configHash = hash(path.join(root,'capacitor.config.json'));
    const dependencyHash = hash(path.join(root,'package-lock.json'));
    const buildId = crypto.createHash('sha256').update(JSON.stringify({version, configHash, dependencyHash, files})).digest('hex');
    return {version, sourceVersion:buildId, buildId, configHash, dependencyHash, files};
}
function verify(native = true) {
    const expected = sourceManifest(), failures = [];
    for (const rel of ['index.html','js/app.js','js/payments.js','js/offline.js','css/base.css','css/payments.css','css/offline.css','sw.js'])
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
    console.log('PASS bundle parity: ' + expected.buildId);
}
module.exports = {sourceManifest, verify};
if (require.main === module) {
    try {verify(!process.argv.includes('--www-only'));}
    catch (error) {console.error(error.message); process.exitCode=1;}
}
