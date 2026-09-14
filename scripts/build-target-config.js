"use strict";
// BUILD-001 — the single source of truth for which backend a generated Cauldra
// bundle points at.
//
// Why this exists: resolveApiBaseUrl() in frontend/js/app.js falls back to a
// compiled-in production constant whenever no override is present, so a native
// build that simply forgets to say what it is targets PRODUCTION silently. That
// is never acceptable for a QA build. Every generated bundle therefore carries
// an explicit, validated target, emitted as js/build-target.js and recorded in
// build-manifest.json, and the parity verifier re-derives the expected bytes
// from this registry rather than trusting the artifact.
//
// There is deliberately no default target: an unstated target is an error, not
// production.
const fs = require('fs'), path = require('path'), crypto = require('crypto');

const root = path.resolve(__dirname, '..');
const REGISTRY_FILE = path.join(__dirname, 'build-targets.json');

// Relative path, inside every bundle tree, of the generated override file.
const EMITTED_FILE = 'js/build-target.js';

function loadRegistry() {
    let raw;
    try { raw = JSON.parse(fs.readFileSync(REGISTRY_FILE, 'utf8')); }
    catch (error) { throw new Error('scripts/build-targets.json missing or unreadable: ' + error.message); }
    if (!raw || typeof raw.targets !== 'object' || raw.targets === null) {
        throw new Error('scripts/build-targets.json must define a "targets" object');
    }
    if (typeof raw.sourceTarget !== 'string' || !raw.targets[raw.sourceTarget]) {
        throw new Error('scripts/build-targets.json "sourceTarget" must name a declared target');
    }
    for (const [name, entry] of Object.entries(raw.targets)) {
        if (!/^[a-z][a-z0-9-]*$/.test(name)) throw new Error('Invalid build target name: ' + name);
        if (!entry || typeof entry.apiBaseUrl !== 'string') {
            throw new Error('Build target "' + name + '" must declare a string apiBaseUrl');
        }
        validateApiBaseUrl(name, entry.apiBaseUrl);
    }
    return raw;
}

// An apiBaseUrl is either "" (same-origin; the web target emits no override at
// all) or a bare https origin. Anything with a path, query, fragment, trailing
// slash, credentials or a non-https scheme is rejected: those silently produce
// wrong request URLs once concatenated with API paths, and http would ship a
// cleartext build.
function validateApiBaseUrl(name, url) {
    if (url === '') return;
    let parsed;
    try { parsed = new URL(url); }
    catch (_) { throw new Error('Build target "' + name + '" apiBaseUrl is not a valid URL: ' + url); }
    const problems = [];
    if (parsed.protocol !== 'https:') problems.push('must use https');
    if (parsed.username || parsed.password) problems.push('must not embed credentials');
    if (parsed.search || parsed.hash) problems.push('must not carry a query or fragment');
    if (parsed.pathname !== '/') problems.push('must not carry a path');
    if (url !== parsed.origin) problems.push('must be exactly the bare origin (' + parsed.origin + '), with no trailing slash');
    if (problems.length) throw new Error('Build target "' + name + '" apiBaseUrl invalid — ' + problems.join('; ') + ': ' + url);
}

function targetNames() { return Object.keys(loadRegistry().targets); }

function sourceTargetName() { return loadRegistry().sourceTarget; }

// Fail closed. A missing or unknown target is an error that names the valid
// options; it never falls back to production.
function resolveTarget(name) {
    const registry = loadRegistry();
    const chosen = (name === undefined || name === null || name === '') ? registry.defaultTarget : name;
    if (chosen === undefined || chosen === null || chosen === '') {
        throw new Error(
            'No build target selected. Pass --target=<name> or set CAULDRA_BUILD_TARGET.\n' +
            'Valid targets: ' + Object.keys(registry.targets).join(', ') + '\n' +
            'There is no default: an unstated target would silently ship the production backend.');
    }
    if (!Object.prototype.hasOwnProperty.call(registry.targets, chosen)) {
        throw new Error('Unknown build target "' + chosen + '". Valid targets: ' + Object.keys(registry.targets).join(', '));
    }
    const entry = registry.targets[chosen];
    validateApiBaseUrl(chosen, entry.apiBaseUrl);
    return {name: chosen, apiBaseUrl: entry.apiBaseUrl, description: entry.description || ''};
}

// Reads the target from argv (--target=x / --target x) then the environment.
function targetFromArgs(argv = process.argv.slice(2), env = process.env) {
    let selected;
    for (let i = 0; i < argv.length; i++) {
        const arg = argv[i];
        if (arg.startsWith('--target=')) selected = arg.slice('--target='.length);
        else if (arg === '--target') selected = argv[++i];
    }
    if (selected === undefined) selected = env.CAULDRA_BUILD_TARGET;
    return resolveTarget(selected);
}

// The exact bytes of the emitted override file. Deterministic: the verifier
// re-renders this and compares byte-for-byte, in www, in the Android assets and
// inside the packaged APK, so a hand-edited bundle cannot pass.
function renderTargetFile(target) {
    const resolved = typeof target === 'string' ? resolveTarget(target) : target;
    const lines = [
        '// GENERATED FILE — do not edit by hand.',
        '// Written by scripts/build-www.js from scripts/build-targets.json (BUILD-001).',
        '// Loaded before js/app.js so resolveApiBaseUrl() sees an explicit backend and',
        '// never falls through to the compiled-in production constant.',
        '//',
        '// Build target: ' + resolved.name,
        'window.CAULDRA_BUILD_TARGET = ' + JSON.stringify(resolved.name) + ';',
    ];
    if (resolved.apiBaseUrl === '') {
        lines.push('// Same-origin target: no API override is set, so resolveApiBaseUrl() uses location.origin.');
    } else {
        lines.push('window.CAULDRA_API_BASE_URL = ' + JSON.stringify(resolved.apiBaseUrl) + ';');
    }
    return Buffer.from(lines.join('\n') + '\n', 'utf8');
}

function renderedHash(target) {
    return crypto.createHash('sha256').update(renderTargetFile(target)).digest('hex');
}

module.exports = {
    root, REGISTRY_FILE, EMITTED_FILE,
    loadRegistry, targetNames, sourceTargetName, resolveTarget, targetFromArgs,
    renderTargetFile, renderedHash, validateApiBaseUrl,
};
