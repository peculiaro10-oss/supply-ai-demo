"use strict";
// BUILD-001 — verify which backend a PACKAGED Android artifact points at,
// before it is installed on anything.
//
// Everything upstream of this (the registry, the emitted override, the parity
// gate) constrains the build. This constrains the thing that actually gets
// installed: it opens the .apk itself and reads the bytes Android will load.
// That is the only check that survives a stale Gradle output directory, a
// mis-copied file, or someone handing you an APK they built last week.
//
// Usage:
//   node scripts/verify-apk-target.js --apk=<path> --target=<qa|production|web>
//   node scripts/verify-apk-target.js --apk=<path>        (prints the target, exits 0)
//
// Exits nonzero, loudly, on any mismatch.
const fs = require('fs'), path = require('path'), zlib = require('zlib'), crypto = require('crypto');
const buildTarget = require('./build-target-config');

const ASSET_PREFIX = 'assets/public/';

// Minimal ZIP central-directory reader. An APK is a ZIP; Node has no bundled
// reader and this script must not depend on anything that might not be
// installed on a release machine. Zip64 is not handled -- these artifacts are
// far below the 4 GB / 65535-entry boundary, and a Zip64 archive fails loudly
// below rather than being misread.
function readZipEntry(buf, entryName) {
    const EOCD_SIG = 0x06054b50, CEN_SIG = 0x02014b50, LOC_SIG = 0x04034b50;
    let eocd = -1;
    const floor = Math.max(0, buf.length - 22 - 0xffff);
    for (let i = buf.length - 22; i >= floor; i--) {
        if (buf.readUInt32LE(i) === EOCD_SIG) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error('Not a readable APK/ZIP: end-of-central-directory record not found');
    const count = buf.readUInt16LE(eocd + 10);
    if (count === 0xffff) throw new Error('Zip64 APK is not supported by this verifier');
    let off = buf.readUInt32LE(eocd + 16);
    if (off === 0xffffffff) throw new Error('Zip64 APK is not supported by this verifier');
    for (let i = 0; i < count; i++) {
        if (buf.readUInt32LE(off) !== CEN_SIG) throw new Error('Corrupt APK central directory at entry ' + i);
        const method = buf.readUInt16LE(off + 10);
        const compressedSize = buf.readUInt32LE(off + 20);
        const nameLen = buf.readUInt16LE(off + 28);
        const extraLen = buf.readUInt16LE(off + 30);
        const commentLen = buf.readUInt16LE(off + 32);
        const localOff = buf.readUInt32LE(off + 42);
        const name = buf.slice(off + 46, off + 46 + nameLen).toString('utf8');
        if (name === entryName) {
            if (buf.readUInt32LE(localOff) !== LOC_SIG) throw new Error('Corrupt local header for ' + name);
            const start = localOff + 30 + buf.readUInt16LE(localOff + 26) + buf.readUInt16LE(localOff + 28);
            const raw = buf.slice(start, start + compressedSize);
            if (method === 0) return Buffer.from(raw);
            if (method === 8) return zlib.inflateRawSync(raw);
            throw new Error('Unsupported compression method ' + method + ' for ' + name);
        }
        off += 46 + nameLen + extraLen + commentLen;
    }
    return null;
}

function arg(name) {
    const hit = process.argv.slice(2).find(a => a.startsWith('--' + name + '='));
    return hit ? hit.slice(name.length + 3) : undefined;
}

function main() {
    const apkPath = arg('apk');
    if (!apkPath) throw new Error('Usage: node scripts/verify-apk-target.js --apk=<path> [--target=<' + buildTarget.targetNames().join('|') + '>]');
    if (!fs.existsSync(apkPath)) throw new Error('APK not found: ' + apkPath);

    const buf = fs.readFileSync(apkPath);
    const failures = [];

    const overrideEntry = ASSET_PREFIX + buildTarget.EMITTED_FILE;
    const override = readZipEntry(buf, overrideEntry);
    if (!override) throw new Error('APK contains no ' + overrideEntry + '. It was not built through the supported target mechanism (BUILD-001).');

    const manifestRaw = readZipEntry(buf, ASSET_PREFIX + 'build-manifest.json');
    if (!manifestRaw) throw new Error('APK contains no ' + ASSET_PREFIX + 'build-manifest.json');
    const manifest = JSON.parse(manifestRaw.toString('utf8'));
    if (!manifest.target) throw new Error('Packaged build-manifest.json declares no target; this APK predates BUILD-001');

    // The packaged manifest names a target; the registry -- not the artifact --
    // says what that name means. Unknown names fail closed here.
    const declared = buildTarget.resolveTarget(manifest.target);
    if (!declared.apiBaseUrl) {
        failures.push('APK declares same-origin target "' + declared.name + '"; native builds must target an explicit backend (qa or production)');
    }
    if (!override.equals(buildTarget.renderTargetFile(declared))) {
        failures.push('packaged ' + buildTarget.EMITTED_FILE + ' does not match the registry rendering of target "' + declared.name + '"');
    }
    if (manifest.apiBaseUrl !== declared.apiBaseUrl) {
        failures.push('packaged manifest apiBaseUrl "' + manifest.apiBaseUrl + '" disagrees with registry target "' + declared.name + '" (' + declared.apiBaseUrl + ')');
    }

    const indexHtml = readZipEntry(buf, ASSET_PREFIX + 'index.html');
    if (!indexHtml) failures.push('APK contains no ' + ASSET_PREFIX + 'index.html');
    else {
        const html = indexHtml.toString('utf8');
        const overrideAt = html.indexOf('src="/' + buildTarget.EMITTED_FILE + '"');
        const appAt = html.indexOf('src="/js/app.js"');
        if (overrideAt < 0) failures.push('packaged index.html does not load /' + buildTarget.EMITTED_FILE);
        else if (appAt >= 0 && overrideAt > appAt) failures.push('packaged index.html loads /' + buildTarget.EMITTED_FILE + ' AFTER /js/app.js, so the override never applies');
    }

    const expectedName = arg('target') || process.env.CAULDRA_BUILD_TARGET;
    if (expectedName) {
        const expected = buildTarget.resolveTarget(expectedName);
        if (expected.name !== declared.name) {
            failures.push('APK targets "' + declared.name + '" (' + (declared.apiBaseUrl || 'same origin') + ') but "' + expected.name + '" (' + (expected.apiBaseUrl || 'same origin') + ') was required');
        }
    }

    const appJs = readZipEntry(buf, ASSET_PREFIX + 'js/app.js');
    console.log('APK:            ' + path.resolve(apkPath));
    console.log('APK SHA-256:    ' + crypto.createHash('sha256').update(buf).digest('hex'));
    console.log('Build target:   ' + declared.name);
    console.log('Backend:        ' + (declared.apiBaseUrl || '(same origin)'));
    console.log('Build ID:       ' + (manifest.buildId || '(none)'));
    if (appJs) console.log('app.js:         ' + appJs.length + ' bytes, sha256 ' + crypto.createHash('sha256').update(appJs).digest('hex'));
    if (expectedName && !failures.length) console.log('Required target "' + expectedName + '" confirmed from the packaged artifact.');

    if (failures.length) throw new Error('APK target verification FAILED:\n  - ' + failures.join('\n  - '));
    console.log('PASS packaged APK target verification');
}

try { main(); }
catch (error) { console.error('\n' + error.message + '\n'); process.exitCode = 1; }
