#!/usr/bin/env node
// Builds frontend/js/i18n-catalog.js from i18n/launch_catalog.json.
//
// i18n/launch_catalog.json is the reviewed source of truth for the launch
// languages' source-text catalog: { "<English UI text>": [fr, es, ar, pt] }.
// Placeholders are written {0}, {1}, ... or {name}; named ones are converted to
// positional ones in source order. A placeholder written {#0} / {#name}, and
// any placeholder named {count}, matches digits only (counts, amounts) — so a
// pattern such as "{#0} Warehouse" can never match data like "Main Warehouse".
// Each translation must use only placeholders that exist in its English source. Run after editing the catalog:
//   node scripts/build-i18n-catalog.js
'use strict';
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const SRC = path.join(root, 'i18n', 'launch_catalog.json');
const OUT = path.join(root, 'frontend', 'js', 'i18n-catalog.js');
const LANGS = ['fr', 'es', 'ar', 'pt'];

const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&nbsp;': ' ', '&mdash;': '—', '&ndash;': '–', '&hellip;': '…', '&rarr;': '→', '&larr;': '←', '&middot;': '·', '&check;': '✓', '&times;': '×' };
const decode = s => s.replace(/&[a-z#0-9]+;/g, e => ENTITIES[e] || e);
const norm = s => decode(String(s)).replace(/\s+/g, ' ').trim();

function positional(src, translations) {
    const names = [];
    const srcOut = src.replace(/\{(#?)([A-Za-z_][\w]*|\d+)\}/g, (_, hash, n) => {
        if (!names.includes(n)) names.push(n);
        return `{${hash || n === 'count' ? '#' : ''}${names.indexOf(n)}}`;
    });
    const trOut = translations.map((t, i) => {
        if (t == null || t === '') return '';
        return norm(t).replace(/\{#?([A-Za-z_][\w]*|\d+)\}/g, (whole, n) => {
            const k = names.indexOf(n);
            if (k < 0) throw new Error(`"${src}" [${LANGS[i]}]: placeholder {${n}} is not in the English source`);
            return `{${k}}`;
        });
    });
    return [srcOut, trOut];
}

function build() {
    const catalog = JSON.parse(fs.readFileSync(SRC, 'utf8'));
    const exact = [], patterns = [], seen = new Set();
    for (const [rawSrc, rawTr] of Object.entries(catalog)) {
        if (!Array.isArray(rawTr) || rawTr.length !== LANGS.length) throw new Error(`"${rawSrc}" must have ${LANGS.length} translations (${LANGS.join(', ')})`);
        const [src, tr] = positional(norm(rawSrc), rawTr);
        if (seen.has(src)) continue;
        seen.add(src);
        (/\{#?\d+\}/.test(src) ? patterns : exact).push([src, ...tr]);
    }
    // Longer patterns first, so a specific sentence wins over a shorter, looser one.
    patterns.sort((a, b) => b[0].replace(/\{#?\d+\}/g, '').length - a[0].replace(/\{#?\d+\}/g, '').length);
    const body = '/* GENERATED FILE — do not edit by hand. Source: i18n/launch_catalog.json; build: node scripts/build-i18n-catalog.js */\n'
        + 'window.CAULDRA_I18N_CATALOG = ' + JSON.stringify({ langs: LANGS, exact, patterns }) + ';\n';
    fs.writeFileSync(OUT, body);
    return { exact: exact.length, patterns: patterns.length, bytes: Buffer.byteLength(body) };
}

module.exports = { build, SRC, OUT };
if (require.main === module) {
    const r = build();
    console.log(`i18n catalog: ${r.exact} exact + ${r.patterns} patterns -> ${path.relative(root, OUT)} (${Math.round(r.bytes / 1024)} KB)`);
}
